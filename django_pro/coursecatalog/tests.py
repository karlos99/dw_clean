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
    CatalogPlacement,
    CatalogTag,
)
from coursecatalog.cte_pathways import pathways_for_course_ids
from coursecatalog.placement_ops import upsert_placement_for_tag
from coursecatalog.services.matching import match_schedule_row


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
