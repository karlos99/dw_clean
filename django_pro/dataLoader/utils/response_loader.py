"""
Response loader for importing survey responses into the database.
Optimized for bulk operations and progress tracking.
"""
from typing import List, Dict, Optional, Set, Tuple
from dataclasses import dataclass
from django.db import transaction, connection
from django.utils import timezone
import threading

from dataLoader.models import (
    SurveyDefinition, SurveyQuestion, SurveyResponse, ImportLog,
    ResponseScaleLabel, TestingWindow, ImportStatus
)
from dataLoader.utils.column_analyzer import AnalysisResult, QuestionColumn, parse_csv_rows
from dataLoader.utils.student_matcher import StudentMatcher, MatchResult


@dataclass
class LoaderConfig:
    """Configuration for the response loader."""
    update_existing: bool = True  # Update existing responses or skip
    skip_unmatched_students: bool = True  # Skip rows where student not found
    batch_size: int = 1000  # Batch size for bulk operations
    progress_update_interval: int = 50  # Update progress every N rows


class ResponseLoader:
    """
    Loads survey responses from CSV into the database.
    
    Optimized for bulk operations:
    - Pre-fetches existing responses for batch checking
    - Uses bulk_create with update_on_conflict
    - Updates progress in batches for polling
    """
    
    def __init__(
        self,
        import_log: ImportLog,
        analysis: AnalysisResult,
        config: Optional[LoaderConfig] = None
    ):
        self.import_log = import_log
        self.analysis = analysis
        self.config = config or LoaderConfig()
        self.survey_definition: Optional[SurveyDefinition] = None
        self.questions: Dict[str, SurveyQuestion] = {}
        self.matcher: Optional[StudentMatcher] = None
        self._stop_flag = False
    
    def create_survey_definition(self, user, grade_range_override: str = '') -> SurveyDefinition:
        """Create or get the SurveyDefinition for this import."""
        survey_type = self.analysis.detected_survey_type or 'Unknown'
        grade_range = grade_range_override.strip() if grade_range_override.strip() else (self.analysis.detected_grade_range or '')
        
        existing = SurveyDefinition.objects.filter(
            survey_type=survey_type,
            school_year=self.import_log.school_year,
            testing_window=self.import_log.testing_window,
            grade_range=grade_range,
        ).first()
        
        if existing:
            self.survey_definition = existing
            existing.version += 1
            existing.save(update_fields=['version', 'updated_at'])
        else:
            self.survey_definition = SurveyDefinition.objects.create(
                name=self.import_log.name,
                survey_type=survey_type,
                school_year=self.import_log.school_year,
                testing_window=self.import_log.testing_window,
                grade_range=grade_range,
                source_system='Panorama',
                created_by=user,
            )
        
        self.import_log.survey_definition = self.survey_definition
        self.import_log.save(update_fields=['survey_definition'])
        
        return self.survey_definition
    
    def create_questions(self) -> Dict[str, SurveyQuestion]:
        """Create SurveyQuestion records for detected questions."""
        if not self.survey_definition:
            raise ValueError("Must call create_survey_definition first")
        
        self.questions = {}
        labels_to_create = []
        
        for i, q in enumerate(self.analysis.questions):
            question, created = SurveyQuestion.objects.get_or_create(
                survey_definition=self.survey_definition,
                value_column_name=q.value_column,
                defaults={
                    'question_text': q.question_text,
                    'text_column_name': q.text_column,
                    'order': i,
                    'response_scale_max': q.response_scale_max,
                    'domain': q.domain,
                }
            )
            
            if not created:
                question.question_text = q.question_text
                question.text_column_name = q.text_column
                question.response_scale_max = q.response_scale_max
                question.save()
            
            # Collect scale labels for bulk create
            if q.scale_labels:
                for value, label in q.scale_labels.items():
                    labels_to_create.append(
                        ResponseScaleLabel(question=question, value=value, label=label)
                    )
            
            self.questions[q.value_column] = question
        
        # Bulk create scale labels with update on conflict
        if labels_to_create:
            ResponseScaleLabel.objects.bulk_create(
                labels_to_create,
                update_conflicts=True,
                unique_fields=['question', 'value'],
                update_fields=['label']
            )
        
        self.import_log.questions_detected = len(self.questions)
        self.import_log.save(update_fields=['questions_detected'])
        
        return self.questions
    
    def load_responses(
        self,
        file_content: str,
        progress_callback=None
    ) -> ImportLog:
        """
        Load all responses from CSV using bulk operations.
        Updates progress in the ImportLog for polling.
        """
        if not self.survey_definition or not self.questions:
            raise ValueError("Must call create_survey_definition and create_questions first")
        
        self.matcher = StudentMatcher(self.import_log.school_year)
        
        self.import_log.status = ImportStatus.PROCESSING
        self.import_log.save(update_fields=['status'])
        
        # Parse all rows
        rows = list(parse_csv_rows(file_content, self.analysis.header_row_index))
        total_rows = len(rows)
        self.import_log.total_rows = total_rows
        self.import_log.save(update_fields=['total_rows'])
        
        # Statistics
        created_count = 0
        updated_count = 0
        skipped_count = 0
        matched_students: Set[str] = set()
        unmatched_students: Set[str] = set()
        unmatched_info: List[Dict] = []  # detailed info for unmatched students (first occurrence per student)
        errors = []
        
        # Process in batches
        batch_size = self.config.batch_size
        
        for batch_start in range(0, total_rows, batch_size):
            if self._stop_flag:
                break
                
            batch_end = min(batch_start + batch_size, total_rows)
            batch_rows = rows[batch_start:batch_end]
            
            # Process batch
            batch_result = self._process_batch(
                batch_rows, 
                batch_start,
                matched_students, 
                unmatched_students,
                unmatched_info,
                errors
            )
            
            created_count += batch_result['created']
            updated_count += batch_result['updated']
            skipped_count += batch_result['skipped']
            
            # Update progress
            self.import_log.records_processed = batch_end
            self.import_log.records_created = created_count
            self.import_log.records_updated = updated_count
            self.import_log.records_skipped = skipped_count
            self.import_log.students_matched = len(matched_students)
            self.import_log.students_unmatched = len(unmatched_students)
            self.import_log.error_count = len(errors)
            self.import_log.save(update_fields=[
                'records_processed', 'records_created', 'records_updated',
                'records_skipped', 'students_matched', 'students_unmatched',
                'error_count'
            ])
            
            if progress_callback:
                progress_callback(batch_end, total_rows)
        
        # Finalize
        self.import_log.error_log = errors[:100]
        # Save unmatched student details (capped at 500, first occurrence per student)
        self.import_log.warnings = unmatched_info[:500]
        self.import_log.status = ImportStatus.COMPLETED if not errors else ImportStatus.PARTIAL
        self.import_log.completed_at = timezone.now()
        self.import_log.save()
        
        return self.import_log
    
    def _process_batch(
        self,
        rows: List[Dict],
        row_offset: int,
        matched_students: Set[str],
        unmatched_students: Set[str],
        unmatched_info: List[Dict],
        errors: List[Dict]
    ) -> Dict[str, int]:
        """Process a batch of rows using bulk operations."""
        responses_to_create = []
        student_data = {}  # student_id -> (ssid, is_matched)
        
        # First pass: match all students and collect response data
        for row_idx, row in enumerate(rows):
            row_num = row_offset + row_idx + 1
            try:
                student_id = row.get('Student ID', '').strip()
                if not student_id:
                    continue

                school_code = row.get('School ID', '').strip()

                # Match student (cache results)
                if student_id not in student_data:
                    match_result = self.matcher.match_student(
                        student_id=student_id,
                        first_name=row.get('Student First Name', ''),
                        last_name=row.get('Student Last Name', ''),
                        grade=row.get('Student Grade Level', ''),
                        school=row.get('School Name', ''),
                    )
                    if match_result.matched:
                        matched_students.add(student_id)
                        student_data[student_id] = (match_result.ssid, True)
                    else:
                        unmatched_students.add(student_id)
                        # Record details for this student (first occurrence only, cap at 500)
                        if len(unmatched_info) < 500:
                            unmatched_info.append({
                                'row': row_num,
                                'student_id': student_id,
                                'first_name': row.get('Student First Name', '').strip(),
                                'last_name': row.get('Student Last Name', '').strip(),
                                'grade': row.get('Student Grade Level', '').strip(),
                                'school': row.get('School Name', '').strip(),
                                'reason': match_result.error or 'No matching student found in the database',
                            })
                        if self.config.skip_unmatched_students:
                            student_data[student_id] = ('', False)
                        else:
                            student_data[student_id] = ('', True)  # Allow with empty ssid

                ssid, allow = student_data[student_id]
                if not allow:
                    continue

                # Create response objects for each question
                for value_col, question in self.questions.items():
                    value_str = row.get(value_col, '').strip()
                    value_int = int(value_str) if value_str and value_str.isdigit() else None

                    responses_to_create.append(SurveyResponse(
                        student_id=student_id,
                        ssid=ssid,
                        school_code=school_code,
                        question=question,
                        value=value_int,
                        import_log=self.import_log,
                    ))
                    
            except Exception as e:
                errors.append({
                    'row': row_num,
                    'error': str(e),
                    'student_id': row.get('Student ID', 'unknown')
                })
        
        # Bulk create with update on conflict
        created = 0
        updated = 0
        
        if responses_to_create:
            with transaction.atomic():
                # Deduplicate by (student_id, question_id) — keep last occurrence.
                # Some files have duplicate student rows (multiple submissions); PostgreSQL
                # cannot ON CONFLICT DO UPDATE the same row twice in one statement.
                deduped: dict = {}
                for r in responses_to_create:
                    deduped[(r.student_id, r.question_id)] = r
                responses_to_create = list(deduped.values())

                # Use bulk_create with update_conflicts
                result = SurveyResponse.objects.bulk_create(
                    responses_to_create,
                    update_conflicts=True,
                    unique_fields=['student_id', 'question'],
                    update_fields=['ssid', 'school_code', 'value', 'import_log']
                )
                # Note: Django doesn't tell us which were created vs updated
                # We estimate based on batch size
                created = len(result)
        
        skipped = len(rows) - len(set(r.student_id for r in responses_to_create))
        
        return {
            'created': created,
            'updated': updated,
            'skipped': skipped
        }
    
    def stop(self):
        """Signal the loader to stop processing."""
        self._stop_flag = True


def run_import_async(import_log_id: int, file_content: str, analysis_data: dict, user_id: int, grade_range_override: str = ''):
    """
    Run import in a background thread.
    This allows the view to return immediately while import continues.
    """
    from django.contrib.auth import get_user_model
    from dataLoader.utils.column_analyzer import QuestionColumn
    
    def _run():
        # Close any existing database connections in this thread
        connection.close()
        
        try:
            import_log = ImportLog.objects.get(pk=import_log_id)
            User = get_user_model()
            user = User.objects.get(pk=user_id)
            
            # Reconstruct analysis
            analysis = AnalysisResult(
                questions=[
                    QuestionColumn(
                        question_text=q['question_text'],
                        value_column=q['value_column'],
                        text_column=q['text_column'],
                        value_column_index=q['value_column_index'],
                        text_column_index=q['text_column_index'],
                        response_scale_max=q.get('response_scale_max', 7),
                        scale_labels=q.get('scale_labels', {}),
                    )
                    for q in analysis_data['questions']
                ],
                demographic_columns=analysis_data['demographic_columns'],
                metadata_columns=analysis_data['metadata_columns'],
                detected_survey_type=analysis_data['detected_survey_type'],
                detected_grade_range=analysis_data['detected_grade_range'],
                header_row_index=analysis_data['header_row_index'],
                total_columns=analysis_data['total_columns'],
            )
            
            loader = ResponseLoader(import_log, analysis)
            loader.create_survey_definition(user, grade_range_override=grade_range_override)
            loader.create_questions()
            loader.load_responses(file_content)
            
        except Exception as e:
            import_log = ImportLog.objects.get(pk=import_log_id)
            import_log.status = ImportStatus.FAILED
            import_log.error_log = [{'error': str(e)}]
            import_log.completed_at = timezone.now()
            import_log.save()
        finally:
            connection.close()
    
    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    return thread
