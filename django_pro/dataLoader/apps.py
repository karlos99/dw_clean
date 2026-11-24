from django.apps import AppConfig


def _handle_dataloader_cache_bump(sender, **kwargs):
    update_fields = kwargs.get('update_fields')
    if update_fields and set(update_fields) <= {'records_processed'}:
        return
    from clickhouse_utils.client import bump_dataloader_sync_version
    bump_dataloader_sync_version()


class DataloaderConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'dataLoader'
    verbose_name = 'Data Loader'

    def ready(self):
        from django.db.models.signals import post_delete, post_save
        from .models import (
            APImportLog,
            IBImportLog,
            ImportLog,
            PSATImportLog,
            SATImportLog,
            SATSettings,
            SurveyDefinition,
        )

        for model in (
            SurveyDefinition,
            ImportLog,
            APImportLog,
            SATImportLog,
            IBImportLog,
            PSATImportLog,
            SATSettings,
        ):
            uid = f'dataloader_cache_bump_{model.__name__}'
            post_save.connect(_handle_dataloader_cache_bump, sender=model, dispatch_uid=f'{uid}_save')
            post_delete.connect(_handle_dataloader_cache_bump, sender=model, dispatch_uid=f'{uid}_del')
