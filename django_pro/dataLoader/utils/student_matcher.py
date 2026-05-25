"""
Student matcher utility for matching CSV student records to existing database records.
"""
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from django.db.models import Q


@dataclass
class MatchResult:
    """Result of attempting to match a student."""
    csv_student_id: str
    csv_first_name: str
    csv_last_name: str
    csv_grade: str
    csv_school: str
    matched: bool = False
    ssid: str = ''
    stu_annual_key: str = ''
    match_method: str = ''  # 'student_id', 'ssid', 'name_grade', etc.
    confidence: float = 0.0
    error: str = ''


@dataclass 
class MatchingStats:
    """Statistics from a batch matching operation."""
    total_records: int = 0
    matched: int = 0
    unmatched: int = 0
    match_by_student_id: int = 0
    match_by_ssid: int = 0
    match_by_name: int = 0
    errors: List[str] = field(default_factory=list)
    unmatched_students: List[MatchResult] = field(default_factory=list)


class StudentMatcher:
    """
    Matches CSV student records to existing students in the database.
    
    Primary matching strategy:
    1. Match by Student ID (local ID) - most reliable
    2. Match by SSID (state ID) if available
    3. Match by name + grade + school as fallback
    """
    
    def __init__(self, school_year: str):
        """
        Initialize matcher for a specific school year.
        
        Args:
            school_year: School year to match against (e.g., '2025-2026')
        """
        self.school_year = school_year
        self._student_cache: Dict[str, Tuple[str, str]] = {}  # student_id -> (ssid, stu_annual_key)
        self._ssid_cache: Dict[str, str] = {}  # ssid -> stu_annual_key
        self._loaded = False
    
    def _load_cache(self):
        """Load student lookup cache from database."""
        if self._loaded:
            return
        
        from studata.models import CurrentStudent
        
        # Load all students for the school year
        students = CurrentStudent.objects.filter(
            school_year=self.school_year
        ).values('student_id', 'ssid', 'stu_annual_key')
        
        for stu in students:
            student_id = str(stu['student_id']).strip()
            ssid = str(stu['ssid']).strip() if stu['ssid'] else ''
            stu_annual_key = stu['stu_annual_key']
            
            if student_id:
                self._student_cache[student_id] = (ssid, stu_annual_key)
            if ssid:
                self._ssid_cache[ssid] = stu_annual_key
        
        self._loaded = True
    
    def match_student(
        self,
        student_id: str,
        first_name: str = '',
        last_name: str = '',
        grade: str = '',
        school: str = '',
        ssid: str = ''
    ) -> MatchResult:
        """
        Attempt to match a single student record.
        
        Args:
            student_id: Local student ID from CSV
            first_name: Student first name
            last_name: Student last name
            grade: Grade level
            school: School name or ID
            ssid: State student ID if available
        
        Returns:
            MatchResult with match details
        """
        self._load_cache()
        
        result = MatchResult(
            csv_student_id=student_id,
            csv_first_name=first_name,
            csv_last_name=last_name,
            csv_grade=grade,
            csv_school=school,
        )
        
        # Strategy 1: Match by local student_id
        student_id_clean = str(student_id).strip()
        if student_id_clean and student_id_clean in self._student_cache:
            cached_ssid, stu_annual_key = self._student_cache[student_id_clean]
            result.matched = True
            result.ssid = cached_ssid
            result.stu_annual_key = stu_annual_key
            result.match_method = 'student_id'
            result.confidence = 1.0
            return result
        
        # Strategy 2: Match by SSID if provided
        ssid_clean = str(ssid).strip() if ssid else ''
        if ssid_clean and ssid_clean in self._ssid_cache:
            result.matched = True
            result.ssid = ssid_clean
            result.stu_annual_key = self._ssid_cache[ssid_clean]
            result.match_method = 'ssid'
            result.confidence = 1.0
            return result
        
        # Strategy 3: Name-based matching (lower confidence, more expensive)
        # Only try this if we have name information
        if first_name and last_name:
            try:
                from studata.models import CurrentStudent
                
                name_matches = CurrentStudent.objects.filter(
                    school_year=self.school_year,
                    student_name__icontains=last_name.strip()
                ).filter(
                    student_name__icontains=first_name.strip()
                )
                
                if grade:
                    name_matches = name_matches.filter(grade=grade.strip())
                
                if name_matches.count() == 1:
                    match = name_matches.first()
                    result.matched = True
                    result.ssid = match.ssid
                    result.stu_annual_key = match.stu_annual_key
                    result.match_method = 'name_grade'
                    result.confidence = 0.8
                    return result
                elif name_matches.count() > 1:
                    result.error = f"Multiple name matches found ({name_matches.count()})"
            except Exception as e:
                result.error = f"Name matching error: {str(e)}"
        
        # No match found
        result.matched = False
        if not result.error:
            result.error = "No matching student found"
        
        return result
    
    def match_batch(self, records: List[Dict]) -> Tuple[List[MatchResult], MatchingStats]:
        """
        Match a batch of student records.
        
        Args:
            records: List of dicts with student info (student_id, first_name, last_name, etc.)
        
        Returns:
            Tuple of (list of MatchResults, MatchingStats)
        """
        self._load_cache()
        
        results = []
        stats = MatchingStats(total_records=len(records))
        
        for record in records:
            result = self.match_student(
                student_id=record.get('student_id', ''),
                first_name=record.get('first_name', ''),
                last_name=record.get('last_name', ''),
                grade=record.get('grade_level', ''),
                school=record.get('school_name', ''),
                ssid=record.get('ssid', ''),
            )
            results.append(result)
            
            if result.matched:
                stats.matched += 1
                if result.match_method == 'student_id':
                    stats.match_by_student_id += 1
                elif result.match_method == 'ssid':
                    stats.match_by_ssid += 1
                elif result.match_method == 'name_grade':
                    stats.match_by_name += 1
            else:
                stats.unmatched += 1
                stats.unmatched_students.append(result)
                if result.error:
                    stats.errors.append(f"Student {record.get('student_id', 'unknown')}: {result.error}")
        
        return results, stats
    
    def get_match_rate(self, results: List[MatchResult]) -> float:
        """Calculate match rate as percentage."""
        if not results:
            return 0.0
        matched = sum(1 for r in results if r.matched)
        return (matched / len(results)) * 100
