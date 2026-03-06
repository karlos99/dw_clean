from django.contrib.auth.signals import user_logged_out
from django.dispatch import receiver
from django.utils import timezone

from .models import UsageSession, PageVisit


@receiver(user_logged_out)
def close_usage_session_on_logout(sender, request, user, **kwargs):
    if not user or not request:
        return

    if request.session.get('impersonator_id'):
        return

    session_key = request.session.session_key
    if not session_key:
        return

    now = timezone.now()
    usage_session = UsageSession.objects.filter(
        user=user,
        session_key=session_key,
        logout_at__isnull=True,
    ).order_by('-login_at').first()

    if not usage_session:
        return

    visit_id = request.session.get('usage_visit_id')
    if visit_id:
        visit = PageVisit.objects.filter(id=visit_id, session=usage_session).first()
        if visit and not visit.ended_at:
            visit.ended_at = now
            visit.duration_seconds = max(0, int((visit.ended_at - visit.started_at).total_seconds()))
            visit.save(update_fields=['ended_at', 'duration_seconds'])

    usage_session.logout_at = now
    usage_session.last_activity_at = now
    usage_session.total_seconds = max(0, int((usage_session.logout_at - usage_session.login_at).total_seconds()))
    usage_session.save(update_fields=['logout_at', 'last_activity_at', 'total_seconds'])
