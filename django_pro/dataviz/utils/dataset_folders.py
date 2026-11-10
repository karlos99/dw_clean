"""Folder paths for DataViz datasets (same `` / `` convention as pages)."""

from django.db.models import Q

from dataviz.utils.page_publish import normalize_page_folder


def folder_contents_q(field: str, path: str):
    """Match *field* equal to *path* or nested under it."""
    path = normalize_page_folder(path)
    return Q(**{field: path}) | Q(**{f'{field}__startswith': path + ' / '})


def all_dataset_folder_paths() -> list[str]:
    from dataviz.models import DataVizDatasetFolder, Dataset

    paths = set(DataVizDatasetFolder.objects.values_list('path', flat=True))
    paths.update(Dataset.objects.exclude(folder='').values_list('folder', flat=True))
    return sorted(p for p in paths if p)


def rewrite_dataset_folder_prefix(old_path: str, new_path: str) -> int:
    """Rename a folder prefix on datasets and folder records. Returns datasets updated."""
    from dataviz.models import DataVizDatasetFolder, Dataset

    old_path = normalize_page_folder(old_path)
    new_path = normalize_page_folder(new_path)
    if not old_path or not new_path or old_path == new_path:
        return 0

    def rewritten(current: str) -> str:
        if current == old_path:
            return new_path
        if current.startswith(old_path + ' / '):
            return new_path + current[len(old_path):]
        return current

    updated = 0
    for ds in Dataset.objects.filter(folder_contents_q('folder', old_path)):
        nxt = rewritten(ds.folder or '')
        if nxt != ds.folder:
            ds.folder = nxt
            ds.save(update_fields=['folder', 'updated_at'])
            updated += 1

    recs = list(DataVizDatasetFolder.objects.filter(folder_contents_q('path', old_path)))
    new_paths = {rewritten(r.path) for r in recs}
    if recs:
        DataVizDatasetFolder.objects.filter(pk__in=[r.pk for r in recs]).delete()
    for path in new_paths:
        DataVizDatasetFolder.objects.get_or_create(path=path)
    DataVizDatasetFolder.objects.get_or_create(path=new_path)
    return updated


def delete_folder_and_datasets(path: str) -> int:
    """Delete a folder, nested folders, and datasets inside. Returns datasets deleted."""
    from dataviz.models import DataVizDatasetFolder, Dataset

    path = normalize_page_folder(path)
    if not path:
        return 0
    datasets = list(Dataset.objects.filter(folder_contents_q('folder', path)))
    for ds in datasets:
        ds.delete()
    DataVizDatasetFolder.objects.filter(folder_contents_q('path', path)).delete()
    return len(datasets)
