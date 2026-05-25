"""
Views for the dataLoader app.
Handles CSV upload, preview, and import for survey data.
"""
import hashlib
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.http import JsonResponse
from django.views.generic import ListView, DetailView, View
from django.urls import reverse
from django.utils import timezone
from django.core.cache import cache

from .access_control import project_admin_required, ProjectAdminRequiredMixin
from .models import SurveyDefinition, SurveyQuestion, SurveyResponse, ImportLog, TestingWindow, ImportStatus, SurveyTopic
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


class DataLoaderDashboardView(ProjectAdminRequiredMixin, ListView):
    """Dashboard showing recent imports and upload option."""
    model = ImportLog
    template_name = 'dataLoader/dashboard.html'
    context_object_name = 'imports'
    paginate_by = 20
    
    def get_queryset(self):
        return ImportLog.objects.select_related(
            'survey_definition', 'uploaded_by'
        ).order_by('-uploaded_at')
    
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context['survey_definitions'] = SurveyDefinition.objects.filter(
            is_active=True
        ).order_by('-school_year')
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
        cache.delete_pattern('*survey_columns_v7*')
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
    cache.delete_pattern('*survey_columns_v7*')
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
    cache.delete_pattern('*survey_columns_v7*')
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
    return JsonResponse({'success': True})
