from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from coursecatalog.catalog_search import (
    expand_semester_pair_terms,
    filter_catalog_entries,
    parse_catalog_search_terms,
)
from coursecatalog.forms import BulkCatalogPlacementForm, CatalogTagForm
from coursecatalog.models import (
    CatalogCategory,
    CatalogDiscipline,
    CatalogLevel,
    CatalogPath,
    CatalogPathColumn,
    CatalogPathStep,
    CatalogPlacement,
    CatalogTag,
)
from coursecatalog.cte_pathways import (
    cte_pathway_detail,
    cte_stage_year,
    group_cte_pathway_rows,
    list_cte_pathways,
    pathways_for_course_ids,
)
from coursecatalog.placement_ops import upsert_placement_for_tag
from coursecatalog.services.matching import match_schedule_row
from coursecatalog.views import seed_default_path_columns


class CatalogSearchTests(SimpleTestCase):
    def test_parse_terms_splits_pasted_ids(self):
        raw = 'ABC123, DEF456\nGHI789'
        self.assertEqual(parse_catalog_search_terms(raw), ['ABC123', 'DEF456', 'GHI789'])

    def test_parse_terms_strips_spreadsheet_quotes(self):
        self.assertEqual(parse_catalog_search_terms('"6005A"\n6051B'), ['6005A', '6051B'])

    def test_expand_semester_pair_when_only_one_half(self):
        self.assertEqual(expand_semester_pair_terms(['6005A']), ['6005A', '6005B'])
        self.assertEqual(expand_semester_pair_terms(['6051B']), ['6051B', '6051A'])

    def test_expand_semester_pair_skips_when_both_present(self):
        self.assertEqual(
            expand_semester_pair_terms(['6005A', '6005B']),
            ['6005A', '6005B'],
        )

    def test_expand_ignores_non_semester_ids(self):
        self.assertEqual(expand_semester_pair_terms(['261R']), ['261R'])

    def test_filter_single_semester_id_expands_to_exact_pair(self):
        from unittest.mock import MagicMock

        base_qs = MagicMock()
        filtered = MagicMock()
        filtered.distinct.return_value = 'pair_result'
        base_qs.filter.return_value = filtered

        result = filter_catalog_entries(queryset=base_qs, q='6005A')

        self.assertEqual(result, 'pair_result')
        q_obj = base_qs.filter.call_args[0][0]
        q_sql = str(q_obj)
        self.assertIn('6005A', q_sql)
        self.assertIn('6005B', q_sql)
        self.assertIn('iexact', q_sql)

    def test_filter_multi_id_uses_exact_match_and_distinct(self):
        from unittest.mock import MagicMock

        base_qs = MagicMock()
        filtered = MagicMock()
        filtered.distinct.return_value = 'multi_result'
        base_qs.filter.return_value = filtered

        result = filter_catalog_entries(queryset=base_qs, q='100\nSC-B')

        self.assertEqual(result, 'multi_result')
        base_qs.filter.assert_called_once()
        filtered.distinct.assert_called_once()
        q_obj = base_qs.filter.call_args[0][0]
        q_sql = str(q_obj)
        self.assertIn('course_key__iexact', q_sql)
        self.assertIn('course_id__iexact', q_sql)
        self.assertIn('state_course_code__iexact', q_sql)

    def test_filter_single_term_uses_broad_icontains(self):
        from unittest.mock import MagicMock

        base_qs = MagicMock()
        base_qs.filter.return_value = 'single_result'

        result = filter_catalog_entries(queryset=base_qs, q='dance')

        self.assertEqual(result, 'single_result')
        q_obj = base_qs.filter.call_args[0][0]
        self.assertIn('course_title__icontains', str(q_obj))
        self.assertNotIn('iexact', str(q_obj))


class ScheduleMatchTests(SimpleTestCase):
    def test_match_schedule_row_from_dict(self):
        from unittest.mock import MagicMock, patch

        entry = MagicMock()
        qs = MagicMock()
        qs.first.side_effect = [None, entry]
        with patch('coursecatalog.services.matching.CourseCatalogEntry') as mock_model:
            mock_model.objects.filter.return_value = qs
            result = match_schedule_row({'course_key': 'K1', 'course_code': 'C1'})
        self.assertIs(result, entry)


class CtePathwayTests(SimpleTestCase):
    def test_pathways_for_course_ids_groups_by_course(self):
        from unittest.mock import MagicMock, patch

        row = MagicMock(
            course_id='6005A',
            career_cluster='Arts, Media, and Entertainment',
            cte_pathway='Professional Music',
            cte_course_level='Concentration',
        )
        qs = MagicMock()
        qs.order_by.return_value = [row]
        with patch('coursecatalog.cte_pathways.CtePathwayEntry') as mock_model:
            mock_model.objects.filter.return_value = qs
            result = pathways_for_course_ids(['6005A'])
        self.assertIn('6005A', result)
        self.assertEqual(len(result['6005A']), 1)
        self.assertEqual(result['6005A'][0].tag_name, 'CTE')


class CteStageGroupingTests(SimpleTestCase):
    def test_stage_year_mapping(self):
        self.assertEqual(cte_stage_year('Introduction'), 1)
        self.assertEqual(cte_stage_year('concentration'), 2)
        self.assertEqual(cte_stage_year('CAPSTONE'), 3)
        self.assertIsNone(cte_stage_year('Elective'))

    def test_group_orders_stage_columns(self):
        rows = [
            {'career_cluster': 'AME', 'cte_pathway': 'Theater', 'cte_course_level': 'Capstone', 'course_id': '3', 'course_title': 'III'},
            {'career_cluster': 'AME', 'cte_pathway': 'Theater', 'cte_course_level': 'Introduction', 'course_id': '1', 'course_title': 'I'},
            {'career_cluster': 'AME', 'cte_pathway': 'Theater', 'cte_course_level': 'Concentration', 'course_id': '2', 'course_title': 'II'},
        ]
        out = group_cte_pathway_rows(rows)
        self.assertEqual(len(out), 1)
        self.assertEqual(
            [c['level'] for c in out[0]['columns']],
            ['Introduction', 'Concentration', 'Capstone'],
        )
        self.assertEqual([c['year'] for c in out[0]['columns']], [1, 2, 3])
        self.assertEqual(out[0]['course_count'], 3)

    def test_group_dedupes_courses(self):
        row = {'career_cluster': 'AME', 'cte_pathway': 'X', 'cte_course_level': 'Introduction', 'course_id': '1', 'course_title': 'I'}
        out = group_cte_pathway_rows([row, dict(row)])
        self.assertEqual(out[0]['columns'][0]['courses'], [{'course_id': '1', 'course_title': 'I'}])


class CtePathwayBrowseTests(SimpleTestCase):
    def test_list_cte_pathways_flattens_tree(self):
        from unittest.mock import patch

        tree = [
            {'name': 'AME', 'pathways': [
                {'name': 'Theater', 'course_count': 3},
                {'name': 'Music', 'course_count': 2},
            ]},
        ]
        with patch('coursecatalog.cte_pathways.build_cte_taxonomy_tree', return_value=tree):
            out = list_cte_pathways()
        self.assertEqual(out, [
            {'career_cluster': 'AME', 'cte_pathway': 'Theater', 'course_count': 3},
            {'career_cluster': 'AME', 'cte_pathway': 'Music', 'course_count': 2},
        ])

    def test_cte_pathway_detail_groups_rows_into_columns(self):
        from unittest.mock import MagicMock, patch

        qs = MagicMock()
        qs.order_by.return_value.values.return_value = [
            {'career_cluster': 'AME', 'cte_pathway': 'Theater', 'cte_course_level': 'Concentration', 'course_id': '2', 'course_title': 'II'},
            {'career_cluster': 'AME', 'cte_pathway': 'Theater', 'cte_course_level': 'Introduction', 'course_id': '1', 'course_title': 'I'},
        ]
        with patch('coursecatalog.cte_pathways.CtePathwayEntry') as mock_model:
            mock_model.objects.filter.return_value = qs
            detail = cte_pathway_detail('AME', 'Theater')
        self.assertEqual([c['level'] for c in detail['columns']], ['Introduction', 'Concentration'])
        self.assertEqual(detail['course_count'], 2)

    def test_cte_pathway_detail_requires_selection(self):
        self.assertIsNone(cte_pathway_detail('', ''))


class CatalogPathColumnTests(TestCase):
    def _path(self):
        tag = CatalogTag.objects.create(name='VAPA', slug='vapa-path')
        cat = CatalogCategory.objects.create(tag=tag, name='Performing Arts', slug='pa-path')
        disc = CatalogDiscipline.objects.create(category=cat, name='Theatre')
        return CatalogPath.objects.create(tag=tag, category=cat, discipline=disc, name='Theatre Path')

    def test_seed_default_columns(self):
        path = self._path()
        seed_default_path_columns(path)
        self.assertEqual(
            list(path.columns.order_by('order').values_list('name', flat=True)),
            ['Introduction', 'Concentration', 'Capstone'],
        )
        self.assertEqual(
            list(path.columns.order_by('order').values_list('year', flat=True)),
            [1, 2, 3],
        )

    def test_seed_is_idempotent(self):
        path = self._path()
        seed_default_path_columns(path)
        seed_default_path_columns(path)
        self.assertEqual(path.columns.count(), 3)

    def test_custom_columns_unlimited(self):
        path = self._path()
        for i in range(6):
            CatalogPathColumn.objects.create(path=path, name=f'Column {i}', order=i)
        self.assertEqual(path.columns.count(), 6)

    def test_step_sequence_unique_per_column(self):
        from django.db import IntegrityError

        path = self._path()
        col = CatalogPathColumn.objects.create(path=path, name='Introduction', order=0)
        CatalogPathStep.objects.create(path=path, column=col, catalog_entry_id='k1', sequence=1)
        CatalogPathStep.objects.create(path=path, column=col, catalog_entry_id='k2', sequence=2)
        with self.assertRaises(IntegrityError):
            CatalogPathStep.objects.create(path=path, column=col, catalog_entry_id='k3', sequence=1)

    def test_same_course_allowed_in_different_columns(self):
        path = self._path()
        c1 = CatalogPathColumn.objects.create(path=path, name='A', order=0)
        c2 = CatalogPathColumn.objects.create(path=path, name='B', order=1)
        CatalogPathStep.objects.create(path=path, column=c1, catalog_entry_id='k1', sequence=1)
        CatalogPathStep.objects.create(path=path, column=c2, catalog_entry_id='k1', sequence=1)
        self.assertEqual(path.steps.count(), 2)

    def test_step_form_lists_all_columns_for_path(self):
        from coursecatalog.forms import CatalogPathStepForm

        path = self._path()
        seed_default_path_columns(path)
        form = CatalogPathStepForm(path=path)
        self.assertEqual(
            list(form.fields['column'].queryset),
            list(path.columns.order_by('order', 'id')),
        )


class CatalogTaxonomyTests(TestCase):
    def test_global_level_unique_name(self):
        from django.db import IntegrityError

        CatalogLevel.objects.create(name='Intro')
        with self.assertRaises(IntegrityError):
            CatalogLevel.objects.create(name='Intro')

    def test_level_form_activates_new_levels(self):
        from coursecatalog.forms import CatalogLevelForm

        form = CatalogLevelForm(data={'name': 'AP Studio', 'code': '', 'order': ''})
        self.assertTrue(form.is_valid(), form.errors)
        level = form.save()
        self.assertTrue(level.is_active)

    def test_tag_only_placement_valid(self):
        tag = CatalogTag.objects.create(name='VAPA', slug='vapa-only')
        p = CatalogPlacement(tag=tag, category=None, discipline=None, level=None)
        p.clean()
        self.assertEqual(p.taxonomy_path_parts(), ['VAPA'])

    def test_tag_form_saves_with_name_only(self):
        form = CatalogTagForm(data={'name': 'VAPA', 'slug': ''})
        self.assertTrue(form.is_valid(), form.errors)
        tag = form.save()
        self.assertEqual(tag.name, 'VAPA')
        self.assertTrue(tag.is_active)
        self.assertTrue(CatalogTag.objects.filter(pk=tag.pk, is_active=True).exists())

    def test_tag_category_discipline_chain(self):
        tag = CatalogTag.objects.create(name='VAPA', slug='vapa')
        cat = CatalogCategory.objects.create(tag=tag, name='Performing Arts', slug='performing-arts')
        disc = CatalogDiscipline.objects.create(category=cat, name='Dance')
        level = CatalogLevel.objects.create(name='Intro')
        p = CatalogPlacement(tag=tag, category=cat, discipline=disc, level=level)
        p.clean()

    def test_placement_rejects_mismatched_category(self):
        tag = CatalogTag.objects.create(name='VAPA', slug='vapa2')
        other_tag = CatalogTag.objects.create(name='STEM', slug='stem')
        cat = CatalogCategory.objects.create(tag=other_tag, name='Robotics', slug='robotics')
        disc = CatalogDiscipline.objects.create(category=cat, name='Build')
        level = CatalogLevel.objects.create(name='Intro')
        p = CatalogPlacement(tag=tag, category=cat, discipline=disc, level=level)
        with self.assertRaises(ValidationError):
            p.clean()

    def test_bulk_form_validates_taxonomy_chain(self):
        tag = CatalogTag.objects.create(name='VAPA', slug='vapa-bulk')
        cat = CatalogCategory.objects.create(tag=tag, name='Performing Arts', slug='pa')
        disc = CatalogDiscipline.objects.create(category=cat, name='Dance')
        level = CatalogLevel.objects.create(name='Intro')
        form = BulkCatalogPlacementForm(
            data={
                'tag': tag.id,
                'category': cat.id,
                'discipline': disc.id,
                'level': level.id,
                'course_keys': ['key1'],
            },
            course_choices=[('key1', 'Course 1')],
        )
        self.assertTrue(form.is_valid())

    def test_upsert_replaces_existing_placement_for_same_tag(self):
        tag = CatalogTag.objects.create(name='VAPA', slug='vapa-upsert')
        cat_a = CatalogCategory.objects.create(tag=tag, name='Arts A', slug='arts-a')
        cat_b = CatalogCategory.objects.create(tag=tag, name='Arts B', slug='arts-b')
        p = CatalogPlacement(
            catalog_entry_id='course-key-1',
            tag=tag,
            category=cat_a,
            discipline=None,
            level=None,
        )
        p.clean()
        p.save()
        outcome = upsert_placement_for_tag(
            catalog_entry_id='course-key-1',
            tag=tag,
            category=cat_b,
            discipline=None,
            level=None,
        )
        self.assertEqual(outcome, 'replaced')
        self.assertEqual(CatalogPlacement.objects.filter(catalog_entry_id='course-key-1', tag=tag).count(), 1)
        self.assertEqual(
            CatalogPlacement.objects.get(catalog_entry_id='course-key-1', tag=tag).category_id,
            cat_b.id,
        )

    def test_bulk_form_rejects_wrong_category_for_tag(self):
        tag = CatalogTag.objects.create(name='VAPA', slug='vapa-x')
        other = CatalogTag.objects.create(name='Other', slug='other')
        cat = CatalogCategory.objects.create(tag=other, name='X', slug='x')
        disc = CatalogDiscipline.objects.create(category=cat, name='Y')
        level = CatalogLevel.objects.create(name='Intro')
        form = BulkCatalogPlacementForm(
            data={
                'tag': tag.id,
                'category': cat.id,
                'discipline': disc.id,
                'level': level.id,
                'course_keys': ['key1'],
            },
            course_choices=[('key1', 'Course 1')],
        )
        self.assertFalse(form.is_valid())
