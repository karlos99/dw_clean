"""
Views for the dataLoader app.
Handles CSV upload, preview, and import for survey data.
"""
import hashlib
import json
import os
import tempfile
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.views.generic import ListView, DetailView, View, TemplateView
from django.urls import reverse
from django.utils import timezone
from django.core.cache import cache
from django.core.paginator import Paginator
from django.conf import settings

from .access_control import project_admin_required, ProjectAdminRequiredMixin
from .models import (
    SurveyDefinition, SurveyQuestion, SurveyResponse, ImportLog,
    TestingWindow, ImportStatus, SurveyTopic,
    APExamCode, APAwardType, APImportLog, APStudentAward, APExamResult, APImportStatus,
    SATImportLog, SATResult, SATSettings, SATImportStatus,
    SAT_PERCENTILE_REGIONS, SAT_PERCENTILE_METRICS,
)
from .utils.column_analyzer import analyze_csv, AnalysisResult
from .utils.response_loader import ResponseLoader, LoaderConfig


# School year choices - generate dynamically
def get_school_year_choices():
    """Generate school year choices (current year +/- 2 years)."""
    from datetime import date
    current_year = date.today().year
    choices = []
    for year in range(current_year - 2, current_year + 2):
        sy = f"{year}-{year + 1}"
        choices.append((sy, sy))
    return choices


def _match_rate(matched, unmatched):
    total = (matched or 0) + (unmatched or 0)
    if total <= 0:
        return None
    return round((matched or 0) / total * 100)


def _status_css(status):
    return {
        'COMPLETED': 'bg-green-100 text-green-800',
        'FAILED': 'bg-red-100 text-red-800',
        'PROCESSING': 'bg-blue-100 text-blue-800',
        'PARTIAL': 'bg-amber-100 text-amber-800',
        'PENDING': 'bg-slate-100 text-slate-800',
    }.get(status, 'bg-slate-100 text-slate-800')


def _normalize_survey_import(obj):
    return {
        'type': 'survey',
        'type_label': 'Survey',
        'type_badge': 'bg-blue-100 text-blue-800',
        'name': obj.name,
        'filename': obj.filename,
        'school_year': obj.school_year,
        'metric_label': 'Records',
        'metric_value': obj.records_processed,
        'metric_note': obj.get_testing_window_display() or '',
        'status': obj.status,
        'status_display': obj.get_status_display(),
        'status_css': _status_css(obj.status),
        'match_rate': _match_rate(obj.students_matched, obj.students_unmatched),
        'is_published': obj.ready_for_exploration,
        'uploaded_at': obj.uploaded_at,
        'detail_url': reverse('dataLoader:import_detail', args=[obj.pk]),
        'progress_url': reverse('dataLoader:import_progress', args=[obj.pk]) if obj.status == 'PROCESSING' else None,
        'delete_url': reverse('dataLoader:delete_import', args=[obj.pk]),
        'publish_url': reverse('dataLoader:quick_toggle_publish_import', args=[obj.pk]) if obj.status == 'COMPLETED' else None,
        'publish_css': 'bg-blue-600' if obj.ready_for_exploration else 'bg-slate-200',
    }


def _normalize_ap_import(obj):
    return {
        'type': 'ap',
        'type_label': 'AP',
        'type_badge': 'bg-purple-100 text-purple-800',
        'name': obj.name,
        'filename': obj.filename,
        'school_year': obj.school_year,
        'metric_label': 'Exams',
        'metric_value': obj.exams_created,
        'metric_note': f"+{obj.exams_skipped} skipped" if obj.exams_skipped else '',
        'status': obj.status,
        'status_display': obj.get_status_display(),
        'status_css': _status_css(obj.status),
        'match_rate': _match_rate(obj.students_matched, obj.students_unmatched),
        'is_published': obj.is_published,
        'uploaded_at': obj.uploaded_at,
        'detail_url': reverse('dataLoader:ap_import_detail', args=[obj.pk]),
        'progress_url': reverse('dataLoader:ap_import_progress', args=[obj.pk]) if obj.status == 'PROCESSING' else None,
        'delete_url': reverse('dataLoader:ap_delete_import', args=[obj.pk]),
        'publish_url': reverse('dataLoader:ap_toggle_publish', args=[obj.pk]) if obj.status == 'COMPLETED' else None,
        'publish_css': 'bg-purple-600' if obj.is_published else 'bg-slate-200',
    }


def _normalize_sat_import(obj):
    return {
        'type': 'sat',
        'type_label': 'SAT',
        'type_badge': 'bg-emerald-100 text-emerald-800',
        'name': obj.name,
        'filename': obj.filename,
        'school_year': obj.school_year,
        'metric_label': 'Records',
        'metric_value': obj.records_created,
        'metric_note': f"+{obj.records_skipped} skipped" if obj.records_skipped else '',
        'status': obj.status,
        'status_display': obj.get_status_display(),
        'status_css': _status_css(obj.status),
        'match_rate': _match_rate(obj.students_matched, obj.students_unmatched),
        'is_published': obj.is_published,
        'uploaded_at': obj.uploaded_at,
        'detail_url': reverse('dataLoader:sat_import_detail', args=[obj.pk]),
        'progress_url': reverse('dataLoader:sat_import_progress', args=[obj.pk]) if obj.status == 'PROCESSING' else None,
        'delete_url': reverse('dataLoader:sat_delete_import', args=[obj.pk]),
        'publish_url': reverse('dataLoader:sat_toggle_publish', args=[obj.pk]) if obj.status == 'COMPLETED' else None,
        'publish_css': 'bg-emerald-600' if obj.is_published else 'bg-slate-200',
    }


# Registry of data loaders shown on the dashboard. New assessment/survey
# loaders can be added here without touching the template layout.
def _build_loader_cards():
    survey_count = ImportLog.objects.count()
    survey_published = ImportLog.objects.filter(ready_for_exploration=True).count()
    ap_count = APImportLog.objects.count()
    ap_published = APImportLog.objects.filter(is_published=True).count()
    sat_count = SATImportLog.objects.count()
    sat_published = SATImportLog.objects.filter(is_published=True).count()

    return [
        {
            'key': 'survey',
            'name': 'Surveys',
            'description': 'Panorama SEL skills, competencies, and other survey CSV exports.',
            'upload_url': reverse('dataLoader:upload'),
            'settings_url': reverse('dataLoader:survey_settings'),
            'accent': 'blue',
            'button_css': 'bg-blue-600 hover:bg-blue-700 focus:ring-blue-500',
            'icon_bg': 'bg-blue-100',
            'icon_css': 'text-blue-600',
            'count': survey_count,
            'published': survey_published,
        },
        {
            'key': 'ap',
            'name': 'AP Exams',
            'description': 'College Board AP Student Datafile with exam results and awards.',
            'upload_url': reverse('dataLoader:ap_upload'),
            'accent': 'purple',
            'button_css': 'bg-purple-600 hover:bg-purple-700 focus:ring-purple-500',
            'icon_bg': 'bg-purple-100',
            'icon_css': 'text-purple-600',
            'count': ap_count,
            'published': ap_published,
        },
        {
            'key': 'sat',
            'name': 'SAT',
            'description': 'College Board SAT results with scores and percentile regions.',
            'upload_url': reverse('dataLoader:sat_upload'),
            'settings_url': reverse('dataLoader:sat_settings'),
            'accent': 'emerald',
            'button_css': 'bg-emerald-600 hover:bg-emerald-700 focus:ring-emerald-500',
            'icon_bg': 'bg-emerald-100',
            'icon_css': 'text-emerald-600',
            'count': sat_count,
            'published': sat_published,
        },
    ]


class DataLoaderDashboardView(ProjectAdminRequiredMixin, TemplateView):
    """Dashboard: loader cards + a unified, browsable list of recent imports."""
    template_name = 'dataLoader/dashboard.html'
    page_size = 25

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        survey_imports = list(
            ImportLog.objects.select_related('survey_definition', 'uploaded_by')
            .order_by('-uploaded_at')[:100]
        )
        ap_imports = list(
            APImportLog.objects.select_related('uploaded_by').order_by('-uploaded_at')[:100]
        )
        sat_imports = list(
            SATImportLog.objects.select_related('uploaded_by').order_by('-uploaded_at')[:100]
        )

        recent = (
            [_normalize_survey_import(o) for o in survey_imports]
            + [_normalize_ap_import(o) for o in ap_imports]
            + [_normalize_sat_import(o) for o in sat_imports]
        )
        recent.sort(key=lambda r: r['uploaded_at'], reverse=True)

        type_filter = self.request.GET.get('type', '').strip().lower()
        if type_filter in ('survey', 'ap', 'sat'):
            recent = [r for r in recent if r['type'] == type_filter]

        paginator = Paginator(recent, self.page_size)
        page_obj = paginator.get_page(self.request.GET.get('page'))

        context['loader_cards'] = _build_loader_cards()
        context['page_obj'] = page_obj
        context['recent_imports'] = page_obj.object_list
        context['is_paginated'] = page_obj.has_other_pages()
        context['total_import_count'] = (
            ImportLog.objects.count() + APImportLog.objects.count() + SATImportLog.objects.count()
        )
        context['published_import_count'] = (
            ImportLog.objects.filter(ready_for_exploration=True).count()
            + APImportLog.objects.filter(is_published=True).count()
            + SATImportLog.objects.filter(is_published=True).count()
        )
        context['survey_definitions'] = SurveyDefinition.objects.filter(
            is_active=True
        ).order_by('-school_year')
        context['type_filter'] = type_filter
        context['type_counts'] = {
            'all': ImportLog.objects.count() + APImportLog.objects.count() + SATImportLog.objects.count(),
            'survey': ImportLog.objects.count(),
            'ap': APImportLog.objects.count(),
            'sat': SATImportLog.objects.count(),
        }
        return context


class UploadView(ProjectAdminRequiredMixin, View):
    """Handle CSV file upload and initial analysis."""
    template_name = 'dataLoader/upload.html'
    
    def get(self, request):
        context = {
            'school_year_choices': get_school_year_choices(),
            'testing_window_choices': TestingWindow.choices,
        }
        return render(request, self.template_name, context)
    
    def post(self, request):
        # Get form data
        name = request.POST.get('name', '').strip()
        school_year = request.POST.get('school_year', '')
        testing_window = request.POST.get('testing_window', '')
        csv_file = request.FILES.get('csv_file')
        
        # Validate
        errors = []
        if not name:
            errors.append("Upload name is required")
        if not school_year:
            errors.append("School year is required")
        if not testing_window:
            errors.append("Testing window is required")
        if not csv_file:
            errors.append("CSV file is required")
        
        if errors:
            for error in errors:
                messages.error(request, error)
            return redirect('dataLoader:upload')
        
        # Read and analyze file
        try:
            file_content = csv_file.read().decode('utf-8-sig')  # Handle BOM
        except UnicodeDecodeError:
            csv_file.seek(0)
            file_content = csv_file.read().decode('latin-1')
        
        # Compute file hash
        file_hash = hashlib.sha256(file_content.encode()).hexdigest()
        
        # Analyze CSV
        analysis = analyze_csv(file_content, csv_file.name)
        
        # Store in session for preview
        request.session['upload_data'] = {
            'name': name,
            'school_year': school_year,
            'testing_window': testing_window,
            'filename': csv_file.name,
            'file_hash': file_hash,
            'file_content': file_content,
            'analysis': {
                'questions': [
                    {
                        'question_text': q.question_text,
                        'value_column': q.value_column,
                        'text_column': q.text_column,
                        'value_column_index': q.value_column_index,
                        'text_column_index': q.text_column_index,
                        'response_scale_max': q.response_scale_max,
                        'scale_labels': q.scale_labels,
                        'text_only': q.text_only,
                    }
                    for q in analysis.questions
                ],
                'demographic_columns': analysis.demographic_columns,
                'metadata_columns': analysis.metadata_columns,
                'detected_survey_type': analysis.detected_survey_type,
                'detected_grade_range': analysis.detected_grade_range,
                'header_row_index': analysis.header_row_index,
                'total_columns': analysis.total_columns,
                'sample_data': analysis.sample_data,
                'warnings': analysis.warnings,
            }
        }
        
        return redirect('dataLoader:preview')


class PreviewView(ProjectAdminRequiredMixin, View):
    """Preview detected structure before import."""
    template_name = 'dataLoader/preview.html'
    
    def get(self, request):
        upload_data = request.session.get('upload_data')
        if not upload_data:
            messages.warning(request, "No upload data found. Please upload a file first.")
            return redirect('dataLoader:upload')
        
        context = {
            'upload_data': upload_data,
            'analysis': upload_data['analysis'],
        }
        return render(request, self.template_name, context)
    
    def post(self, request):
        """Confirm and start the import asynchronously."""
        upload_data = request.session.get('upload_data')
        if not upload_data:
            messages.error(request, "Upload data expired. Please upload again.")
            return redirect('dataLoader:upload')

        grade_range_override = request.POST.get('grade_range', '').strip()

        # Create ImportLog
        import_log = ImportLog.objects.create(
            name=upload_data['name'],
            school_year=upload_data['school_year'],
            testing_window=upload_data['testing_window'],
            filename=upload_data['filename'],
            file_hash=upload_data['file_hash'],
            uploaded_by=request.user,
            status=ImportStatus.PENDING,
        )
        
        # Start async import
        from .utils.response_loader import run_import_async
        
        run_import_async(
            import_log_id=import_log.pk,
            file_content=upload_data['file_content'],
            analysis_data=upload_data['analysis'],
            user_id=request.user.pk,
            grade_range_override=grade_range_override,
        )
        
        # Clear session
        del request.session['upload_data']
        
        messages.info(request, "Import started! Progress will update automatically.")
        
        return redirect('dataLoader:import_progress', pk=import_log.pk)


class ImportDetailView(ProjectAdminRequiredMixin, DetailView):
    """View details of a completed import."""
    model = ImportLog
    template_name = 'dataLoader/import_detail.html'
    context_object_name = 'import_log'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        import_log = self.object
        
        # Get sample responses
        context['sample_responses'] = SurveyResponse.objects.filter(
            import_log=import_log
        ).select_related('question')[:50]
        
        # Get question summary
        if import_log.survey_definition:
            context['questions'] = import_log.survey_definition.questions.all()
        
        return context


class SurveyDefinitionListView(ProjectAdminRequiredMixin, ListView):
    """List all survey definitions."""
    model = SurveyDefinition
    template_name = 'dataLoader/survey_list.html'
    context_object_name = 'surveys'
    paginate_by = 20
    
    def get_queryset(self):
        return SurveyDefinition.objects.prefetch_related(
            'questions', 'imports'
        ).order_by('-school_year', '-created_at')


class SurveyDefinitionDetailView(ProjectAdminRequiredMixin, DetailView):
    """View details of a survey definition."""
    model = SurveyDefinition
    template_name = 'dataLoader/survey_detail.html'
    context_object_name = 'survey'
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        questions = self.object.questions.select_related('topic').order_by('order')
        context['questions'] = questions
        context['topics'] = list(SurveyTopic.objects.values('id', 'name', 'color').order_by('name'))
        context['imports'] = self.object.imports.order_by('-uploaded_at')[:10]
        context['response_count'] = SurveyResponse.objects.filter(
            question__survey_definition=self.object
        ).count()
        
        # Detect which scales are used in this survey
        scales_used = set()
        for q in questions:
            scale_max = q.response_scale_max or 5
            scales_used.add(scale_max)
        
        # Default to scale 5 if no questions
        if not scales_used:
            scales_used = {5}
        
        context['scales_used'] = sorted(scales_used)
        context['has_scale_5'] = 5 in scales_used
        context['has_scale_7'] = 7 in scales_used
        context['scale_5_range'] = range(1, 6)
        context['scale_7_range'] = range(1, 8)
        
        # Default colors for each scale (gradient from red to green)
        context['default_colors_5'] = {
            '1': '#ef4444',  # red
            '2': '#f97316',  # orange
            '3': '#eab308',  # yellow
            '4': '#84cc16',  # lime
            '5': '#22c55e',  # green
        }
        context['default_colors_7'] = {
            '1': '#ef4444',  # red
            '2': '#f97316',  # orange
            '3': '#facc15',  # yellow
            '4': '#a3e635',  # lime
            '5': '#22c55e',  # green
            '6': '#14b8a6',  # teal
            '7': '#0ea5e9',  # sky blue
        }
        
        return context


class SurveySettingsView(ProjectAdminRequiredMixin, View):
    """
    Survey settings: select a survey, then assign each of its questions to a
    category (SurveyTopic). Assignments are saved via the existing
    assign_question_topic AJAX endpoint so the Data Exploration grouping updates.
    """
    template_name = 'dataLoader/survey_settings.html'

    def get(self, request):
        surveys = SurveyDefinition.objects.order_by(
            '-school_year', 'survey_type', 'testing_window', 'grade_range'
        )

        selected = None
        selected_pk = request.GET.get('survey', '').strip()
        if selected_pk.isdigit():
            selected = SurveyDefinition.objects.filter(pk=int(selected_pk)).first()
        if selected is None:
            selected = surveys.first()

        questions = []
        assigned_count = 0
        if selected:
            questions = list(selected.questions.select_related('topic').order_by('order'))
            assigned_count = sum(1 for q in questions if q.topic_id)

        context = {
            'surveys': surveys,
            'selected': selected,
            'questions': questions,
            'topics': SurveyTopic.objects.order_by('name'),
            'assigned_count': assigned_count,
            'unassigned_count': len(questions) - assigned_count,
        }
        return render(request, self.template_name, context)


# Function-based view for HTMX file upload progress (optional enhancement)
@project_admin_required
def check_import_status(request, pk):
    """AJAX endpoint to check import status."""
    import_log = get_object_or_404(ImportLog, pk=pk)
    
    # Calculate progress percentage
    progress = 0
    if import_log.total_rows > 0:
        progress = int((import_log.records_processed / import_log.total_rows) * 100)
    
    return JsonResponse({
        'status': import_log.status,
        'status_display': import_log.get_status_display(),
        'records_processed': import_log.records_processed,
        'total_rows': import_log.total_rows,
        'records_created': import_log.records_created,
        'records_updated': import_log.records_updated,
        'records_skipped': import_log.records_skipped,
        'students_matched': import_log.students_matched,
        'students_unmatched': import_log.students_unmatched,
        'questions_detected': import_log.questions_detected,
        'error_count': import_log.error_count,
        'progress': progress,
        'is_complete': import_log.status in [ImportStatus.COMPLETED, ImportStatus.FAILED, ImportStatus.PARTIAL],
    })


class ImportProgressView(ProjectAdminRequiredMixin, DetailView):
    """View showing import progress with auto-updating progress bar."""
    model = ImportLog
    template_name = 'dataLoader/import_progress.html'
    context_object_name = 'import_log'


@project_admin_required
def delete_import(request, pk):
    """Delete an import and all associated responses."""
    import_log = get_object_or_404(ImportLog, pk=pk)
    
    if request.method == 'POST':
        name = import_log.name
        
        # Count responses before deletion
        response_count = SurveyResponse.objects.filter(import_log=import_log).count()
        
        # Delete the import log (responses will cascade delete)
        import_log.delete()
        
        messages.success(
            request, 
            f"Deleted import '{name}' and {response_count:,} associated responses."
        )
        return redirect('dataLoader:dashboard')
    
    # GET request - show confirmation page
    response_count = SurveyResponse.objects.filter(import_log=import_log).count()
    
    return render(request, 'dataLoader/delete_confirm.html', {
        'import_log': import_log,
        'response_count': response_count,
    })


@project_admin_required
def delete_survey(request, pk):
    """Delete a survey definition and all associated data."""
    survey = get_object_or_404(SurveyDefinition, pk=pk)
    
    if request.method == 'POST':
        name = survey.name
        
        # Count related data
        question_count = survey.questions.count()
        response_count = SurveyResponse.objects.filter(
            question__survey_definition=survey
        ).count()
        
        # Delete the survey (questions and responses will cascade)
        survey.delete()
        
        messages.success(
            request, 
            f"Deleted survey '{name}', {question_count} questions, "
            f"and {response_count:,} responses."
        )
        return redirect('dataLoader:survey_list')
    
    # GET request - show confirmation page
    question_count = survey.questions.count()
    response_count = SurveyResponse.objects.filter(
        question__survey_definition=survey
    ).count()
    import_count = survey.imports.count()
    
    return render(request, 'dataLoader/delete_survey_confirm.html', {
        'survey': survey,
        'question_count': question_count,
        'response_count': response_count,
        'import_count': import_count,
    })


@project_admin_required
def toggle_publish(request, pk):
    """Toggle whether a survey is published for data exploration."""
    survey = get_object_or_404(SurveyDefinition, pk=pk)
    
    if request.method == 'POST':
        if survey.ready_for_exploration:
            # Unpublish
            survey.ready_for_exploration = False
            survey.published_at = None
            survey.published_by = None
            survey.save(update_fields=['ready_for_exploration', 'published_at', 'published_by'])
            messages.success(request, f"'{survey.name}' has been unpublished from Data Exploration.")
        else:
            # Publish
            survey.ready_for_exploration = True
            survey.published_at = timezone.now()
            survey.published_by = request.user
            survey.save(update_fields=['ready_for_exploration', 'published_at', 'published_by'])
            messages.success(request, f"'{survey.name}' is now available in Data Exploration.")
        
        # Invalidate the survey columns cache so data exploration reflects the change immediately
        cache.delete_pattern('*survey_columns_v8*')
        return redirect('dataLoader:survey_detail', pk=pk)
    
    # GET - show confirmation page
    response_count = SurveyResponse.objects.filter(
        question__survey_definition=survey
    ).count()
    
    return render(request, 'dataLoader/toggle_publish_confirm.html', {
        'survey': survey,
        'response_count': response_count,
        'action': 'unpublish' if survey.ready_for_exploration else 'publish',
    })


@project_admin_required
def quick_toggle_publish(request, pk):
    """Quick toggle publish status from survey list (no confirmation)."""
    if request.method != 'POST':
        return redirect('dataLoader:survey_list')
    
    survey = get_object_or_404(SurveyDefinition, pk=pk)
    
    if survey.ready_for_exploration:
        # Unpublish
        survey.ready_for_exploration = False
        survey.published_at = None
        survey.published_by = None
        survey.save(update_fields=['ready_for_exploration', 'published_at', 'published_by'])
        messages.success(request, f"'{survey.name}' has been unpublished.")
    else:
        # Publish
        survey.ready_for_exploration = True
        survey.published_at = timezone.now()
        survey.published_by = request.user
        survey.save(update_fields=['ready_for_exploration', 'published_at', 'published_by'])
        messages.success(request, f"'{survey.name}' is now published to Data Exploration.")
    
    # Invalidate the survey columns cache so data exploration reflects the change immediately
    cache.delete_pattern('*survey_columns_v8*')
    return redirect('dataLoader:survey_list')


@project_admin_required
def quick_toggle_publish_import(request, pk):
    """Quick toggle publish status for an import from the dashboard (no confirmation)."""
    if request.method != 'POST':
        return redirect('dataLoader:dashboard')
    
    import_log = get_object_or_404(ImportLog, pk=pk)
    
    # Only allow publishing completed imports
    if import_log.status != 'COMPLETED':
        messages.error(request, "Only completed imports can be published.")
        return redirect('dataLoader:dashboard')
    
    if import_log.ready_for_exploration:
        # Unpublish
        import_log.ready_for_exploration = False
        import_log.published_at = None
        import_log.published_by = None
        import_log.save(update_fields=['ready_for_exploration', 'published_at', 'published_by'])
        messages.success(request, f"'{import_log.name}' has been unpublished from Data Exploration.")
    else:
        # Publish
        import_log.ready_for_exploration = True
        import_log.published_at = timezone.now()
        import_log.published_by = request.user
        import_log.save(update_fields=['ready_for_exploration', 'published_at', 'published_by'])
        messages.success(request, f"'{import_log.name}' is now published to Data Exploration.")
    
    # Invalidate the survey columns cache so data exploration reflects the change immediately
    cache.delete_pattern('*survey_columns_v8*')
    return redirect('dataLoader:dashboard')


@project_admin_required
def save_response_colors(request, pk):
    """Save response color configuration for a survey definition."""
    import json
    
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)
    
    survey = get_object_or_404(SurveyDefinition, pk=pk)
    
    try:
        data = json.loads(request.body)
        colors = data.get('colors', {})
        
        # Validate colors structure: {'scale_5': {...}, 'scale_7': {...}}
        validated_colors = {}
        
        for scale_key in ['scale_5', 'scale_7']:
            if scale_key in colors and isinstance(colors[scale_key], dict):
                validated_colors[scale_key] = {}
                for value, color in colors[scale_key].items():
                    str_value = str(value)
                    # Validate hex color format
                    if isinstance(color, str) and (color.startswith('#') or color == ''):
                        validated_colors[scale_key][str_value] = color
        
        survey.response_colors = validated_colors
        survey.save(update_fields=['response_colors'])
        
        return JsonResponse({'success': True, 'colors': validated_colors})
    
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=500)


@project_admin_required
def topic_list(request):
    """Global topic management: list all topics (GET) or create one (POST)."""
    import json

    if request.method == 'GET':
        topics = list(SurveyTopic.objects.values('id', 'name', 'color').order_by('name'))
        # Page render or JSON depending on Accept header
        if request.headers.get('Accept') == 'application/json' or request.GET.get('fmt') == 'json':
            return JsonResponse({'topics': topics})
        return render(request, 'dataLoader/topic_list.html', {'topics': SurveyTopic.objects.order_by('name')})

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        name = (data.get('name') or '').strip()
        color = (data.get('color') or '#6366f1').strip()

        if not name:
            return JsonResponse({'error': 'Name is required'}, status=400)
        if not (color.startswith('#') and len(color) == 7):
            color = '#6366f1'
        if SurveyTopic.objects.filter(name__iexact=name).exists():
            return JsonResponse({'error': f'A topic named "{name}" already exists'}, status=400)

        topic = SurveyTopic.objects.create(name=name, color=color)
        return JsonResponse({'topic': {'id': topic.pk, 'name': topic.name, 'color': topic.color}}, status=201)

    return JsonResponse({'error': 'Method not allowed'}, status=405)


@project_admin_required
def update_topic(request, pk):
    """Rename or recolor a global topic."""
    import json

    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    topic = get_object_or_404(SurveyTopic, pk=pk)
    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    name = (data.get('name') or '').strip()
    color = (data.get('color') or topic.color).strip()

    if name and name != topic.name:
        if SurveyTopic.objects.filter(name__iexact=name).exclude(pk=pk).exists():
            return JsonResponse({'error': f'A topic named "{name}" already exists'}, status=400)
        topic.name = name
    if color.startswith('#') and len(color) == 7:
        topic.color = color

    topic.save()
    return JsonResponse({'topic': {'id': topic.pk, 'name': topic.name, 'color': topic.color}})


@project_admin_required
def delete_topic(request, pk):
    """Delete a global topic. Questions using it will have their topic cleared."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    topic = get_object_or_404(SurveyTopic, pk=pk)
    topic.delete()  # SET_NULL on SurveyQuestion.topic clears references
    return JsonResponse({'success': True})


# ── Legacy per-survey topic endpoints (kept for backward compat, delegate to global) ──

@project_admin_required
def manage_survey_topics(request, pk):
    """List all global topics (GET) or create a new global topic (POST)."""
    import json
    get_object_or_404(SurveyDefinition, pk=pk)  # 404 if survey doesn't exist

    if request.method == 'GET':
        topics = list(SurveyTopic.objects.values('id', 'name', 'color').order_by('name'))
        return JsonResponse({'topics': topics})

    if request.method == 'POST':
        try:
            data = json.loads(request.body)
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON'}, status=400)

        name = (data.get('name') or '').strip()
        color = (data.get('color') or '#6366f1').strip()

        if not name:
            return JsonResponse({'error': 'Name is required'}, status=400)
        if not (color.startswith('#') and len(color) == 7):
            color = '#6366f1'
        if SurveyTopic.objects.filter(name__iexact=name).exists():
            return JsonResponse({'error': f'A topic named "{name}" already exists'}, status=400)

        topic = SurveyTopic.objects.create(name=name, color=color)
        return JsonResponse({'topic': {'id': topic.pk, 'name': topic.name, 'color': topic.color}}, status=201)

    return JsonResponse({'error': 'Method not allowed'}, status=405)


@project_admin_required
def existing_survey_topics(request, pk):
    """Return all global topics not yet assigned to any question in this survey (for suggestion UI)."""
    survey = get_object_or_404(SurveyDefinition, pk=pk)
    used_ids = set(
        survey.questions.exclude(topic=None).values_list('topic_id', flat=True)
    )
    topics = list(
        SurveyTopic.objects
        .exclude(id__in=used_ids)
        .values('name', 'color')
        .order_by('name')
    )
    return JsonResponse({'topics': topics})


@project_admin_required
def delete_survey_topic(request, pk, topic_pk):
    """Delete a global topic (legacy URL kept for JS compatibility)."""
    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    get_object_or_404(SurveyDefinition, pk=pk)  # 404 if survey doesn't exist
    topic = get_object_or_404(SurveyTopic, pk=topic_pk)
    topic.delete()
    return JsonResponse({'success': True})


@project_admin_required
def assign_question_topic(request, pk, question_pk):
    """Assign (or clear) a topic on a single survey question."""
    import json

    if request.method != 'POST':
        return JsonResponse({'error': 'POST required'}, status=405)

    survey = get_object_or_404(SurveyDefinition, pk=pk)
    question = get_object_or_404(SurveyQuestion, pk=question_pk, survey_definition=survey)

    try:
        data = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)

    topic_id = data.get('topic_id')  # None / null → clears the topic

    if topic_id:
        topic = get_object_or_404(SurveyTopic, pk=topic_id)
        question.topic = topic
    else:
        question.topic = None

    question.save(update_fields=['topic'])
    # Invalidate the survey columns cache so Data Exploration grouping reflects
    # the new category immediately.
    cache.delete_pattern('*survey_columns_v8*')
    return JsonResponse({'success': True})


# ── AP Test Data Loader views ──

class APUploadView(ProjectAdminRequiredMixin, View):
    template_name = 'dataLoader/ap_upload.html'

    def get(self, request):
        context = {
            'school_year_choices': get_school_year_choices(),
        }
        return render(request, self.template_name, context)

    def post(self, request):
        name = request.POST.get('name', '').strip()
        school_year = request.POST.get('school_year', '')
        csv_file = request.FILES.get('csv_file')

        errors = []
        if not name:
            errors.append("Upload name is required")
        if not school_year:
            errors.append("School year is required")
        if not csv_file:
            errors.append("CSV file is required")

        if errors:
            for error in errors:
                messages.error(request, error)
            return redirect('dataLoader:ap_upload')

        try:
            file_content = csv_file.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            csv_file.seek(0)
            try:
                file_content = csv_file.read().decode('latin-1')
            except Exception as e:
                messages.error(request, f"Could not decode CSV file: {e}")
                return redirect('dataLoader:ap_upload')

        file_hash = hashlib.sha256(file_content.encode()).hexdigest()

        tmp_dir = os.path.join(tempfile.gettempdir(), 'ap_uploads')
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_path = os.path.join(tmp_dir, f"{file_hash}.csv")
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(file_content)

        from .utils.ap_column_analyzer import analyze_ap_csv

        try:
            analysis = analyze_ap_csv(file_content, csv_file.name)
        except Exception as e:
            messages.error(request, f"Could not analyze CSV: {e}")
            return redirect('dataLoader:ap_upload')

        request.session['ap_upload_data'] = {
            'name': name,
            'school_year': school_year,
            'filename': csv_file.name,
            'file_hash': file_hash,
            'tmp_path': tmp_path,
            'analysis': {
                'student_columns': analysis.student_columns,
                'active_exam_blocks': analysis.active_exam_blocks,
                'active_award_blocks': analysis.active_award_blocks,
                'total_students': analysis.total_students,
                'total_exams': analysis.total_exams,
                'total_awards': analysis.total_awards,
                'header_row_index': analysis.header_row_index,
                'total_columns': analysis.total_columns,
                'warnings': analysis.warnings,
                'unique_exam_codes': list(analysis.unique_exam_codes),
                'sample_students': [
                    {
                        'ap_number': s.ap_number,
                        'last_name': s.last_name,
                        'first_name': s.first_name,
                        'dob': s.dob,
                        'grade_level': s.grade_level,
                        'school_name': s.school_name,
                        'gender': s.gender,
                        'exam_count': s.exam_count,
                        'award_count': s.award_count,
                        'exam_names': s.exam_names,
                    }
                    for s in analysis.sample_students
                ],
            }
        }

        return redirect('dataLoader:ap_preview')


class APPreviewView(ProjectAdminRequiredMixin, View):
    template_name = 'dataLoader/ap_preview.html'

    def get(self, request):
        upload_data = request.session.get('ap_upload_data')
        if not upload_data:
            messages.warning(request, "No upload data found. Please upload a file first.")
            return redirect('dataLoader:ap_upload')

        from .utils.ap_student_matcher import APStudentMatcher
        from .utils.ap_column_analyzer import parse_ap_csv_rows, EXAM_CODES, AWARD_TYPES, GRADE_LEVELS, GENDER_MAP

        matcher = APStudentMatcher(upload_data['school_year'])
        analysis = upload_data['analysis']
        diagnostics = []

        db_students = matcher.total_students_loaded
        diagnostics.append(f"Database: {db_students} student records loaded for school year {upload_data['school_year']}")
        if db_students == 0:
            diagnostics.append(f"WARNING: No students found in StudentEnroll for school year '{upload_data['school_year']}'. Check that the school year matches the database.")

        try:
            tmp_path = upload_data.get('tmp_path', '')
            if tmp_path and os.path.exists(tmp_path):
                with open(tmp_path, 'r', encoding='utf-8') as f:
                    file_content = f.read()
            else:
                file_content = upload_data.get('file_content', '')
                if not file_content:
                    diagnostics.append("WARNING: No file content found in session or on disk.")
        except Exception as e:
            diagnostics.append(f"ERROR reading file: {e}")
            file_content = upload_data.get('file_content', '')

        try:
            rows = list(parse_ap_csv_rows(file_content, analysis['header_row_index']))
        except Exception as e:
            diagnostics.append(f"ERROR parsing CSV: {e}")
            rows = []

        diagnostics.append(f"CSV: {len(rows)} student rows parsed, {analysis.get('total_exams', 0)} exam results, {analysis.get('total_awards', 0)} awards")

        matched_count = 0
        unmatched_students = []

        for row in rows:
            ap_number = (row.get('AP Number / AP ID', '') or '').strip()
            if not ap_number:
                continue

            last_name = (row.get('Last Name', '') or '').strip()
            first_name = (row.get('First Name', '') or '').strip()
            dob = (row.get('Date of Birth', '') or '').strip()
            ap_school = (row.get('AI Institution Name', '') or '').strip()

            result = matcher.match_student(
                ap_number=ap_number,
                first_name=first_name,
                last_name=last_name,
                dob=dob,
                ap_school=ap_school,
            )

            if result.matched:
                matched_count += 1
            else:
                unmatched_students.append({
                    'ap_number': ap_number,
                    'last_name': last_name,
                    'first_name': first_name,
                    'dob': dob,
                    'school': ap_school,
                    'reason': result.reason or 'No match found',
                })

        exam_code_names = {}
        for code in analysis.get('unique_exam_codes', []):
            exam_code_names[code] = EXAM_CODES.get(code, f"Unknown ({code})")

        context = {
            'upload_data': upload_data,
            'analysis': analysis,
            'matched_count': matched_count,
            'unmatched_count': len(unmatched_students),
            'unmatched_students': unmatched_students[:200],
            'exam_code_names': exam_code_names,
            'total_students': analysis.get('total_students', 0),
            'total_exams': analysis.get('total_exams', 0),
            'total_awards': analysis.get('total_awards', 0),
            'total_sis_students': db_students,
            'diagnostics': diagnostics,
        }
        return render(request, self.template_name, context)

    def post(self, request):
        upload_data = request.session.get('ap_upload_data')
        if not upload_data:
            messages.error(request, "Upload data expired. Please upload again.")
            return redirect('dataLoader:ap_upload')

        manual_overrides = {}
        for key, value in request.POST.items():
            if key.startswith('ssid_override_') and value.strip():
                ap_number = key.replace('ssid_override_', '')
                manual_overrides[ap_number] = value.strip()

        ap_import_log = APImportLog.objects.create(
            name=upload_data['name'],
            school_year=upload_data['school_year'],
            filename=upload_data['filename'],
            file_hash=upload_data['file_hash'],
            uploaded_by=request.user,
            status=APImportStatus.PENDING,
            manual_overrides=manual_overrides,
        )

        from .utils.ap_response_loader import run_ap_import_async

        tmp_path = upload_data.get('tmp_path', '')
        if tmp_path and os.path.exists(tmp_path):
            with open(tmp_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
        else:
            file_content = upload_data.get('file_content', '')

        run_ap_import_async(
            import_log_id=ap_import_log.pk,
            file_content=file_content,
            analysis_data=upload_data['analysis'],
            user_id=request.user.pk,
        )

        del request.session['ap_upload_data']

        messages.info(request, "AP import started! Progress will update automatically.")
        return redirect('dataLoader:ap_import_progress', pk=ap_import_log.pk)


@project_admin_required
def ap_student_search(request):
    first_name = request.GET.get('first_name', '').strip()
    last_name = request.GET.get('last_name', '').strip()
    school_year = request.GET.get('school_year', '').strip()

    if not school_year:
        return JsonResponse({'error': 'School year is required'}, status=400)

    from .utils.ap_student_matcher import APStudentMatcher
    matcher = APStudentMatcher(school_year)
    results = matcher.search_students(first_name=first_name, last_name=last_name)

    return JsonResponse({'students': results})


class APImportDetailView(ProjectAdminRequiredMixin, DetailView):
    model = APImportLog
    template_name = 'dataLoader/ap_import_detail.html'
    context_object_name = 'import_log'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        import_log = self.object

        context['exam_results'] = APExamResult.objects.filter(
            import_log=import_log
        ).select_related('exam')[:50]

        context['awards'] = APStudentAward.objects.filter(
            import_log=import_log
        ).select_related('award_type')[:50]

        match_rate = 0
        total = import_log.students_matched + import_log.students_unmatched
        if total > 0:
            match_rate = round((import_log.students_matched / total) * 100)
        context['match_rate'] = match_rate

        return context


class APImportProgressView(ProjectAdminRequiredMixin, DetailView):
    model = APImportLog
    template_name = 'dataLoader/ap_import_progress.html'
    context_object_name = 'import_log'


@project_admin_required
def ap_import_status(request, pk):
    import_log = get_object_or_404(APImportLog, pk=pk)

    progress = 0
    if import_log.total_students > 0:
        progress = int((import_log.records_processed / import_log.total_students) * 100)

    return JsonResponse({
        'status': import_log.status,
        'status_display': import_log.get_status_display(),
        'records_processed': import_log.records_processed,
        'total_students': import_log.total_students,
        'exams_detected': import_log.exams_detected,
        'exams_created': import_log.exams_created,
        'exams_skipped': import_log.exams_skipped,
        'awards_detected': import_log.awards_detected,
        'awards_created': import_log.awards_created,
        'awards_skipped': import_log.awards_skipped,
        'students_matched': import_log.students_matched,
        'students_unmatched': import_log.students_unmatched,
        'error_count': import_log.error_count,
        'progress': progress,
        'is_complete': import_log.status in [APImportStatus.COMPLETED, APImportStatus.FAILED, APImportStatus.PARTIAL],
    })


@project_admin_required
def ap_delete_import(request, pk):
    ap_import_log = get_object_or_404(APImportLog, pk=pk)

    if request.method == 'POST':
        name = ap_import_log.name
        exam_count = APExamResult.objects.filter(import_log=ap_import_log).count()
        award_count = APStudentAward.objects.filter(import_log=ap_import_log).count()
        ap_import_log.delete()

        messages.success(
            request,
            f"Deleted AP import '{name}', {exam_count:,} exam results, and {award_count:,} awards."
        )
        return redirect('dataLoader:dashboard')

    exam_count = APExamResult.objects.filter(import_log=ap_import_log).count()
    award_count = APStudentAward.objects.filter(import_log=ap_import_log).count()

    return render(request, 'dataLoader/ap_delete_confirm.html', {
        'import_log': ap_import_log,
        'exam_count': exam_count,
        'award_count': award_count,
    })


@project_admin_required
def ap_toggle_publish(request, pk):
    """Toggle whether an AP import is published (available in the Data Explorer)."""
    ap_import_log = get_object_or_404(APImportLog, pk=pk)

    if request.method != 'POST':
        return redirect('dataLoader:ap_import_detail', pk=pk)

    if ap_import_log.status != APImportStatus.COMPLETED:
        messages.error(request, "Only completed AP imports can be published.")
        return redirect('dataLoader:ap_import_detail', pk=pk)

    if ap_import_log.is_published:
        ap_import_log.is_published = False
        ap_import_log.published_at = None
        ap_import_log.published_by = None
        ap_import_log.save(update_fields=['is_published', 'published_at', 'published_by'])
        messages.success(request, f"'{ap_import_log.name}' unpublished from Data Exploration.")
    else:
        ap_import_log.is_published = True
        ap_import_log.published_at = timezone.now()
        ap_import_log.published_by = request.user
        ap_import_log.save(update_fields=['is_published', 'published_at', 'published_by'])
        messages.success(request, f"'{ap_import_log.name}' is now available in Data Exploration.")

    _invalidate_ap_caches()
    return redirect('dataLoader:ap_import_detail', pk=pk)


def _invalidate_ap_caches():
    """Invalidate cached AP column structures so publish changes reflect immediately."""
    from django.core.cache import cache
    try:
        cache.delete_pattern('ap_columns*')
        cache.delete_pattern('dv_ap_exam_cols*')
        cache.delete_pattern('dv_ap_award_cols*')
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# SAT Results import
# ─────────────────────────────────────────────────────────────────────────────

class SATUploadView(ProjectAdminRequiredMixin, View):
    template_name = 'dataLoader/sat_upload.html'

    def get(self, request):
        context = {
            'school_year_choices': get_school_year_choices(),
        }
        return render(request, self.template_name, context)

    def post(self, request):
        name = request.POST.get('name', '').strip()
        school_year = request.POST.get('school_year', '')
        csv_file = request.FILES.get('csv_file')

        errors = []
        if not name:
            errors.append("Upload name is required")
        if not school_year:
            errors.append("School year is required")
        if not csv_file:
            errors.append("CSV file is required")

        if errors:
            for error in errors:
                messages.error(request, error)
            return redirect('dataLoader:sat_upload')

        try:
            file_content = csv_file.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            csv_file.seek(0)
            try:
                file_content = csv_file.read().decode('latin-1')
            except Exception as e:
                messages.error(request, f"Could not decode CSV file: {e}")
                return redirect('dataLoader:sat_upload')

        file_hash = hashlib.sha256(file_content.encode()).hexdigest()

        tmp_dir = os.path.join(tempfile.gettempdir(), 'sat_uploads')
        os.makedirs(tmp_dir, exist_ok=True)
        tmp_path = os.path.join(tmp_dir, f"{file_hash}.csv")
        with open(tmp_path, 'w', encoding='utf-8') as f:
            f.write(file_content)

        from .utils.sat_column_analyzer import analyze_sat_csv
        from .utils.sat_school_resolver import resolve_school_short_name

        try:
            analysis = analyze_sat_csv(file_content, csv_file.name)
        except Exception as e:
            messages.error(request, f"Could not analyze CSV: {e}")
            return redirect('dataLoader:sat_upload')

        request.session['sat_upload_data'] = {
            'name': name,
            'school_year': school_year,
            'filename': csv_file.name,
            'file_hash': file_hash,
            'tmp_path': tmp_path,
            'analysis': {
                'student_columns': analysis.student_columns,
                'score_columns': analysis.score_columns,
                'percentile_columns': analysis.percentile_columns,
                'total_students': analysis.total_students,
                'total_with_scores': analysis.total_with_scores,
                'total_missing_state_id': analysis.total_missing_state_id,
                'available_percentile_regions': analysis.available_percentile_regions,
                'header_row_index': analysis.header_row_index,
                'total_columns': analysis.total_columns,
                'warnings': analysis.warnings,
                'sample_students': [
                    {
                        'last_name': s.last_name,
                        'first_name': s.first_name,
                        'state_student_id': s.state_student_id,
                        'cb_id': s.cb_id,
                        'dob': s.dob,
                        'school_code': s.school_code,
                        'school_name': resolve_school_short_name(s.school_name, s.school_code),
                        'test_date': s.test_date,
                        'total_score': s.total_score,
                        'ebrw_score': s.ebrw_score,
                        'math_score': s.math_score,
                    }
                    for s in analysis.sample_students
                ],
            }
        }

        return redirect('dataLoader:sat_preview')


class SATPreviewView(ProjectAdminRequiredMixin, View):
    template_name = 'dataLoader/sat_preview.html'

    def get(self, request):
        upload_data = request.session.get('sat_upload_data')
        if not upload_data:
            messages.warning(request, "No upload data found. Please upload a file first.")
            return redirect('dataLoader:sat_upload')

        from .utils.sat_student_matcher import SATStudentMatcher
        from .utils.sat_column_analyzer import parse_sat_csv_rows, parse_date, parse_int, _to_str
        from .utils.sat_response_loader import build_record_key

        matcher = SATStudentMatcher(upload_data['school_year'])
        analysis = upload_data['analysis']
        diagnostics = []

        db_students = matcher.total_students_loaded
        diagnostics.append(f"Database: {db_students} student records loaded for school year {upload_data['school_year']}")
        if db_students == 0:
            diagnostics.append(
                f"WARNING: No students found in StudentEnroll for school year '{upload_data['school_year']}'. "
                "Check that the school year matches the database."
            )

        try:
            tmp_path = upload_data.get('tmp_path', '')
            if tmp_path and os.path.exists(tmp_path):
                with open(tmp_path, 'r', encoding='utf-8') as f:
                    file_content = f.read()
            else:
                file_content = upload_data.get('file_content', '')
                if not file_content:
                    diagnostics.append("WARNING: No file content found in session or on disk.")
        except Exception as e:
            diagnostics.append(f"ERROR reading file: {e}")
            file_content = upload_data.get('file_content', '')

        try:
            rows = list(parse_sat_csv_rows(file_content, analysis['header_row_index']))
        except Exception as e:
            diagnostics.append(f"ERROR parsing CSV: {e}")
            rows = []

        diagnostics.append(
            f"CSV: {len(rows)} student rows parsed, "
            f"{analysis.get('total_with_scores', 0)} with scores, "
            f"{analysis.get('total_missing_state_id', 0)} missing STATE_STUDENT_ID"
        )

        matched_count = 0
        unmatched_students = []

        from .utils.sat_school_resolver import resolve_school_short_name

        for row in rows:
            state_student_id = _to_str(row.get('STATE_STUDENT_ID', ''))
            cb_id = _to_str(row.get('CB_ID', ''))
            last_name = _to_str(row.get('NAME_LAST', ''))
            first_name = _to_str(row.get('NAME_FIRST', ''))
            school_code = _to_str(row.get('AI_CODE', ''))
            school_name = resolve_school_short_name(_to_str(row.get('AI_NAME', '')), school_code)
            dob_date = parse_date(row.get('BIRTH_DATE', ''))
            test_date = parse_date(row.get('LATEST_SAT_DATE', ''))

            if not state_student_id and not cb_id and not last_name and not first_name:
                continue

            record_key = build_record_key(
                cb_id, state_student_id, last_name, first_name,
                dob_date.isoformat() if dob_date else "",
                test_date.isoformat() if test_date else "",
            )

            result = matcher.match_student(
                record_key=record_key,
                state_student_id=state_student_id,
                first_name=first_name,
                last_name=last_name,
                dob=dob_date.isoformat() if dob_date else "",
                school=school_name,
                cb_id=cb_id,
            )

            if result.matched:
                matched_count += 1
            else:
                unmatched_students.append({
                    'record_key': record_key,
                    'state_student_id': state_student_id,
                    'cb_id': cb_id,
                    'last_name': last_name,
                    'first_name': first_name,
                    'dob': dob_date.isoformat() if dob_date else "",
                    'school': school_name,
                    'total_score': parse_int(row.get('LATEST_SAT_TOTAL', '')),
                    'reason': result.reason or 'No match found',
                })

        context = {
            'upload_data': upload_data,
            'analysis': analysis,
            'matched_count': matched_count,
            'unmatched_count': len(unmatched_students),
            'unmatched_students': unmatched_students[:200],
            'total_students': analysis.get('total_students', 0),
            'total_with_scores': analysis.get('total_with_scores', 0),
            'total_missing_state_id': analysis.get('total_missing_state_id', 0),
            'total_sis_students': db_students,
            'diagnostics': diagnostics,
        }
        return render(request, self.template_name, context)

    def post(self, request):
        upload_data = request.session.get('sat_upload_data')
        if not upload_data:
            messages.error(request, "Upload data expired. Please upload again.")
            return redirect('dataLoader:sat_upload')

        manual_overrides = {}
        for key, value in request.POST.items():
            if key.startswith('ssid_override_') and value.strip():
                record_key = key.replace('ssid_override_', '')
                manual_overrides[record_key] = value.strip()

        sat_import_log = SATImportLog.objects.create(
            name=upload_data['name'],
            school_year=upload_data['school_year'],
            filename=upload_data['filename'],
            file_hash=upload_data['file_hash'],
            uploaded_by=request.user,
            status=SATImportStatus.PENDING,
            manual_overrides=manual_overrides,
        )

        from .utils.sat_response_loader import run_sat_import_async

        tmp_path = upload_data.get('tmp_path', '')
        if tmp_path and os.path.exists(tmp_path):
            with open(tmp_path, 'r', encoding='utf-8') as f:
                file_content = f.read()
        else:
            file_content = upload_data.get('file_content', '')

        run_sat_import_async(
            import_log_id=sat_import_log.pk,
            file_content=file_content,
            analysis_data=upload_data['analysis'],
            user_id=request.user.pk,
        )

        del request.session['sat_upload_data']

        messages.info(request, "SAT import started! Progress will update automatically.")
        return redirect('dataLoader:sat_import_progress', pk=sat_import_log.pk)


@project_admin_required
def sat_student_search(request):
    first_name = request.GET.get('first_name', '').strip()
    last_name = request.GET.get('last_name', '').strip()
    school_year = request.GET.get('school_year', '').strip()

    if not school_year:
        return JsonResponse({'error': 'School year is required'}, status=400)

    from .utils.sat_student_matcher import SATStudentMatcher
    matcher = SATStudentMatcher(school_year)
    results = matcher.search_students(first_name=first_name, last_name=last_name)

    return JsonResponse({'students': results})


class SATImportDetailView(ProjectAdminRequiredMixin, DetailView):
    model = SATImportLog
    template_name = 'dataLoader/sat_import_detail.html'
    context_object_name = 'import_log'

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        import_log = self.object

        context['sat_results'] = SATResult.objects.filter(
            import_log=import_log
        )[:50]

        match_rate = 0
        total = import_log.students_matched + import_log.students_unmatched
        if total > 0:
            match_rate = round((import_log.students_matched / total) * 100)
        context['match_rate'] = match_rate

        return context


class SATImportProgressView(ProjectAdminRequiredMixin, DetailView):
    model = SATImportLog
    template_name = 'dataLoader/sat_import_progress.html'
    context_object_name = 'import_log'


@project_admin_required
def sat_import_status(request, pk):
    import_log = get_object_or_404(SATImportLog, pk=pk)

    progress = 0
    if import_log.total_students > 0:
        progress = int((import_log.records_processed / import_log.total_students) * 100)

    return JsonResponse({
        'status': import_log.status,
        'status_display': import_log.get_status_display(),
        'records_processed': import_log.records_processed,
        'total_students': import_log.total_students,
        'records_detected': import_log.records_detected,
        'records_created': import_log.records_created,
        'records_skipped': import_log.records_skipped,
        'students_matched': import_log.students_matched,
        'students_unmatched': import_log.students_unmatched,
        'error_count': import_log.error_count,
        'progress': progress,
        'is_complete': import_log.status in [
            SATImportStatus.COMPLETED, SATImportStatus.FAILED, SATImportStatus.PARTIAL
        ],
    })


@project_admin_required
def sat_delete_import(request, pk):
    sat_import_log = get_object_or_404(SATImportLog, pk=pk)

    if request.method == 'POST':
        name = sat_import_log.name
        record_count = SATResult.objects.filter(import_log=sat_import_log).count()
        sat_import_log.delete()

        messages.success(
            request,
            f"Deleted SAT import '{name}' and {record_count:,} SAT results."
        )
        return redirect('dataLoader:dashboard')

    record_count = SATResult.objects.filter(import_log=sat_import_log).count()

    return render(request, 'dataLoader/sat_delete_confirm.html', {
        'import_log': sat_import_log,
        'record_count': record_count,
    })


@project_admin_required
def sat_toggle_publish(request, pk):
    """Toggle whether a SAT import is published (available in the Data Explorer)."""
    sat_import_log = get_object_or_404(SATImportLog, pk=pk)

    if request.method != 'POST':
        return redirect('dataLoader:sat_import_detail', pk=pk)

    if sat_import_log.status != SATImportStatus.COMPLETED:
        messages.error(request, "Only completed SAT imports can be published.")
        return redirect('dataLoader:sat_import_detail', pk=pk)

    if sat_import_log.is_published:
        sat_import_log.is_published = False
        sat_import_log.published_at = None
        sat_import_log.published_by = None
        sat_import_log.save(update_fields=['is_published', 'published_at', 'published_by'])
        messages.success(request, f"'{sat_import_log.name}' unpublished from Data Exploration.")
    else:
        sat_import_log.is_published = True
        sat_import_log.published_at = timezone.now()
        sat_import_log.published_by = request.user
        sat_import_log.save(update_fields=['is_published', 'published_at', 'published_by'])
        messages.success(request, f"'{sat_import_log.name}' is now available in Data Exploration.")

    _invalidate_sat_caches()

    from .utils.sat_response_loader import run_sat_clickhouse_sync_async
    run_sat_clickhouse_sync_async()

    return redirect('dataLoader:sat_import_detail', pk=pk)


class SATSettingsView(ProjectAdminRequiredMixin, View):
    """Global SAT publish settings page (percentiles + latest/highest)."""
    template_name = 'dataLoader/sat_settings.html'

    def get(self, request):
        context = {
            'sat_settings': SATSettings.get_solo(),
            'percentile_region_choices': SAT_PERCENTILE_REGIONS,
            'percentile_metric_choices': SAT_PERCENTILE_METRICS,
            'published_import_count': SATImportLog.objects.filter(is_published=True).count(),
        }
        return render(request, self.template_name, context)

    def post(self, request):
        regions = [r for r in request.POST.getlist('percentile_regions') if r in dict(SAT_PERCENTILE_REGIONS)]
        metrics = [m for m in request.POST.getlist('percentile_metrics') if m in dict(SAT_PERCENTILE_METRICS)]
        result_mode = request.POST.get('result_mode', 'latest')
        if result_mode not in ('latest', 'highest'):
            result_mode = 'latest'
        highest_metric = request.POST.get('highest_metric', 'total')
        if highest_metric not in ('total', 'rw', 'math'):
            highest_metric = 'total'

        settings_obj = SATSettings.get_solo()
        settings_obj.percentile_regions = regions
        settings_obj.percentile_metrics = metrics
        settings_obj.result_mode = result_mode
        settings_obj.highest_metric = highest_metric
        settings_obj.updated_by = request.user
        settings_obj.save()

        _invalidate_sat_caches()

        if request.POST.get('republish'):
            from .utils.sat_response_loader import run_sat_clickhouse_sync_async
            run_sat_clickhouse_sync_async()
            messages.success(
                request,
                "SAT settings saved. Re-publishing SAT data to Data Exploration in the background."
            )
        else:
            messages.success(
                request,
                "SAT settings saved. Re-publish SAT data to apply them in Data Exploration."
            )
        return redirect('dataLoader:sat_settings')


def _invalidate_sat_caches():
    """Invalidate cached SAT column structures so publish changes reflect immediately."""
    from django.core.cache import cache
    try:
        cache.delete_pattern('sat_columns*')
        cache.delete_pattern('dv_sat_cols*')
    except Exception:
        pass
