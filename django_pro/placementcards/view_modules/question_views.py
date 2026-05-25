from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render

from placementcards.models import QuestionTemplate
from placementcards.view_modules.card_data_utils import (
    can_user_manage_tags,
    get_request_school,
    invalidate_school_caches,
)
from placementcards.view_modules.card_views import DEFAULT_QUESTIONS


def _is_school_admin(user) -> bool:
    if user.is_superuser:
        return True
    role_names: set[str] = set()
    if hasattr(user, 'active_role') and user.active_role:
        role_names.add(user.active_role.name)
    if hasattr(user, 'roles'):
        role_names.update(user.roles.values_list('name', flat=True))
    return 'School Admin' in role_names


def _is_default_question(question: QuestionTemplate) -> bool:
    # Defaults are created first using sequential display_order from DEFAULT_QUESTIONS.
    return question.display_order < len(DEFAULT_QUESTIONS)


def _question_panel_context(school, user):
    questions = (
        QuestionTemplate.objects.filter(school=school, is_active=True)
        .only('id', 'question_category', 'question_text', 'question_type', 'field_options', 'requires_detail', 'display_order', 'created_by')
        .order_by('question_category', 'display_order', 'id')
    )
    hidden_questions = (
        QuestionTemplate.objects.filter(school=school, is_active=False)
        .only('id', 'question_category', 'question_text', 'question_type', 'display_order', 'created_by')
        .order_by('question_category', 'display_order', 'id')
    )
    non_deletable_default_ids = {question.id for question in questions if _is_default_question(question)}
    return {
        'school': school,
        'questions': questions,
        'hidden_questions': hidden_questions,
        'mode': 'create',
        'question': None,
        'options_value': '',
        'is_school_admin': _is_school_admin(user),
        'non_deletable_default_ids': non_deletable_default_ids,
    }


@login_required
def manage_questions(request: HttpRequest):
    school = get_request_school(request)
    if not can_user_manage_tags(request.user, school):
        return HttpResponse('Forbidden', status=403)

    context = _question_panel_context(school, request.user)
    if request.headers.get('HX-Request') == 'true':
        return render(request, 'placementcards/partials/questions_panel.html', context)
    return render(request, 'placementcards/manage_questions.html', context)


@login_required
def question_form(request: HttpRequest, question_id: int | None = None):
    school = get_request_school(request)
    if not can_user_manage_tags(request.user, school):
        return HttpResponse('Forbidden', status=403)

    question = None
    mode = 'create'
    options_value = ''
    if question_id is not None:
        question = get_object_or_404(QuestionTemplate, id=question_id, school=school, is_active=True)
        mode = 'edit'
        options_value = ', '.join(question.field_options.get('choices', [])) if isinstance(question.field_options, dict) else ''

    return render(
        request,
        'placementcards/partials/question_form.html',
        {
            'mode': mode,
            'question': question,
            'options_value': options_value,
        },
    )


@login_required
@transaction.atomic
def save_question(request: HttpRequest):
    school = get_request_school(request)
    if not can_user_manage_tags(request.user, school):
        return HttpResponse('Forbidden', status=403)

    question_id = request.POST.get('question_id', '').strip()
    question_text = request.POST.get('question_text', '').strip()
    question_category = request.POST.get('question_category', '').strip()
    question_type = request.POST.get('question_type', '').strip()
    option_values = [item.strip() for item in request.POST.get('options', '').split(',') if item.strip()]
    requires_detail = request.POST.get('requires_detail') == 'on'

    if not question_text or not question_category or not question_type:
        return HttpResponse('Missing required fields', status=400)

    payload = {
        'question_text': question_text,
        'question_category': question_category,
        'question_type': question_type,
        'field_options': {'choices': option_values} if option_values else {},
        'requires_detail': requires_detail,
    }

    if question_id.isdigit():
        question = get_object_or_404(QuestionTemplate, id=int(question_id), school=school)
        for key, value in payload.items():
            setattr(question, key, value)
        question.save(update_fields=['question_text', 'question_category', 'question_type', 'field_options', 'requires_detail', 'updated_at'])
    else:
        max_order = QuestionTemplate.objects.filter(school=school).order_by('-display_order').values_list('display_order', flat=True).first() or 0
        QuestionTemplate.objects.create(
            school=school,
            display_order=max_order + 1,
            created_by=request.user,
            **payload,
        )

    invalidate_school_caches(school.pk)
    return render(request, 'placementcards/partials/questions_panel.html', _question_panel_context(school, request.user))


@login_required
@transaction.atomic
def delete_question(request: HttpRequest, question_id: int):
    school = get_request_school(request)
    if not can_user_manage_tags(request.user, school):
        return HttpResponse('Forbidden', status=403)

    question = get_object_or_404(QuestionTemplate, id=question_id, school=school)
    if _is_default_question(question):
        return HttpResponse('Default questions cannot be deleted, only hidden', status=403)
    if question.created_by_id != request.user.id:
        return HttpResponse('Only the creator can delete this question', status=403)

    question.delete()
    invalidate_school_caches(school.pk)
    return render(request, 'placementcards/partials/questions_panel.html', _question_panel_context(school, request.user))


@login_required
@transaction.atomic
def hide_question(request: HttpRequest, question_id: int):
    school = get_request_school(request)
    if not can_user_manage_tags(request.user, school):
        return HttpResponse('Forbidden', status=403)

    question = get_object_or_404(QuestionTemplate, id=question_id, school=school)
    question.is_active = False
    question.save(update_fields=['is_active', 'updated_at'])
    invalidate_school_caches(school.pk)
    return render(request, 'placementcards/partials/questions_panel.html', _question_panel_context(school, request.user))


@login_required
@transaction.atomic
def unhide_question(request: HttpRequest, question_id: int):
    school = get_request_school(request)
    if not can_user_manage_tags(request.user, school):
        return HttpResponse('Forbidden', status=403)

    question = get_object_or_404(QuestionTemplate, id=question_id, school=school)
    question.is_active = True
    question.save(update_fields=['is_active', 'updated_at'])
    invalidate_school_caches(school.pk)
    return render(request, 'placementcards/partials/questions_panel.html', _question_panel_context(school, request.user))
