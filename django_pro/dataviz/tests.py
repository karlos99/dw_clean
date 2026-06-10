from django.test import TransactionTestCase
from django.contrib.auth import get_user_model
from dataviz.models import Dataset, SavedChart, DataVizPage
from dataviz.utils.compiler import compile_dataset_to_sql, SQLCompilationError
from dataviz.utils.chart_aggregator import aggregate_chart_data

User = get_user_model()


class DataVizTestCase(TransactionTestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username='test_admin',
            email='test_admin@example.com',
            password='password123',
            name='Test Admin'
        )

    def test_model_creation(self):
        """Tests standard model creation and to_dict methods."""
        dataset = Dataset.objects.create(
            name='Test Student Dataset',
            description='Current student base',
            base_table='current_student',
            columns=['current_student.ssid', 'current_student.student_name'],
            owner=self.user
        )
        self.assertEqual(dataset.name, 'Test Student Dataset')
        self.assertEqual(len(dataset.to_dict()['columns']), 2)

        chart = SavedChart.objects.create(
            name='Test Chart',
            chart_type='bar',
            source_type='dataset',
            dataset=dataset,
            owner=self.user
        )
        self.assertEqual(chart.chart_type, 'bar')

    def test_compiler_whitelisting(self):
        """Tests that compiler correctly compiles whitelisted columns and blocks invalid columns."""
        dataset = Dataset.objects.create(
            name='Secure Dataset',
            base_table='current_student',
            columns=['current_student.ssid', 'current_student.student_name'],
            owner=self.user
        )
        # Should compile without errors
        sql, params = compile_dataset_to_sql(dataset, user=self.user)
        self.assertIn('"current_student"."ssid"', sql)
        
        # Test validation failures for sql injection or outside whitelist
        invalid_dataset = Dataset.objects.create(
            name='Hack Dataset',
            base_table='current_student',
            columns=['current_student.ssid', 'current_student.password; DROP TABLE current_student;--'],
            owner=self.user
        )
        with self.assertRaises(SQLCompilationError):
            compile_dataset_to_sql(invalid_dataset, user=self.user)

    def test_compiler_right_join(self):
        """Tests that compiler compiles RIGHT JOIN syntax correctly."""
        dataset = Dataset.objects.create(
            name='Right Join Dataset',
            base_table='current_student',
            joins=[{
                'table': 'results',
                'on_left': 'current_student.ssid',
                'on_right': 'results.ssid',
                'join_type': 'right'
            }],
            columns=['current_student.ssid', 'results.ss'],
            owner=self.user
        )
        sql, params = compile_dataset_to_sql(dataset, user=self.user)
        self.assertIn('RIGHT JOIN "results"', sql)

    def test_compiler_multiple_column_join(self):
        """Tests that compiler correctly compiles joins with multiple conditions."""
        dataset = Dataset.objects.create(
            name='Multi Join Dataset',
            base_table='current_student',
            joins=[{
                'table': 'results',
                'join_type': 'inner',
                'conditions': [
                    {'on_left': 'current_student.ssid', 'on_right': 'results.ssid'},
                    {'on_left': 'current_student.school_year', 'on_right': 'results.school_year'}
                ]
            }],
            columns=['current_student.ssid', 'results.ss'],
            owner=self.user
        )
        sql, params = compile_dataset_to_sql(dataset, user=self.user)
        self.assertIn('INNER JOIN "results" ON "current_student"."ssid" = "results"."ssid" AND "current_student"."school_year" = "results"."school_year"', sql)

    def test_compiler_distinct_query(self):
        """Tests that compiler respects the distinct field flag."""
        dataset = Dataset.objects.create(
            name='Distinct Dataset',
            base_table='current_student',
            distinct=True,
            columns=['current_student.ssid'],
            owner=self.user
        )
        sql, params = compile_dataset_to_sql(dataset, user=self.user)
        self.assertTrue(sql.startswith('SELECT DISTINCT'))

        dataset.distinct = False
        dataset.save()
        sql, params = compile_dataset_to_sql(dataset, user=self.user)
        self.assertTrue(sql.startswith('SELECT "current_student"'))

    def test_chart_aggregator(self):
        """Tests that ECharts aggregator correctly groups and aggregates data."""
        rows = [
            {'school': 'School A', 'ssid': 's1', 'gender': 'M'},
            {'school': 'School A', 'ssid': 's2', 'gender': 'F'},
            {'school': 'School B', 'ssid': 's3', 'gender': 'M'},
        ]
        
        options = aggregate_chart_data(
            rows=rows,
            chart_type='bar',
            x_col='school',
            y_col='ssid',
            aggregation='count',
            series_col='gender'
        )
        
        self.assertIn('series', options)
        self.assertEqual(len(options['series']), 2)  # 'M' and 'F' series
        self.assertEqual(options['xAxis']['data'], ['School A', 'School B'])

    def test_api_column_unique_values(self):
        """Tests the unique values fetching API with various inputs and permissions."""
        from django.urls import reverse
        from django.db import connection
        url = reverse('dataviz:api_column_unique_values')

        # 1. Unauthenticated user -> Redirects to login (302)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)

        # 2. Authenticated non-admin user -> 403 Forbidden
        non_admin = User.objects.create_user(
            username='non_admin',
            email='non_admin@example.com',
            password='password123',
            name='Non Admin'
        )
        self.client.force_login(non_admin)
        response = self.client.get(url, {'column': 'current_student.grade'}, headers={'X-Requested-With': 'XMLHttpRequest'})
        self.assertEqual(response.status_code, 403)

        # Log in as superuser (which passes is_user_admin)
        admin_user = User.objects.create_superuser(
            username='admin_test',
            email='admin_test@example.com',
            password='password123',
            name='Admin Test'
        )
        self.client.force_login(admin_user)

        # 3. Missing column parameter -> Returns empty values list
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'values': []})

        # 4. Invalid column format (no dot) -> 400 Bad Request
        response = self.client.get(url, {'column': 'invalidcolumn'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'values': []})

        # 5. Non-whitelisted table -> 400 Bad Request
        response = self.client.get(url, {'column': 'nonexistent_table.some_col'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'values': []})

        # 6. Non-whitelisted column -> 400 Bad Request
        response = self.client.get(url, {'column': 'current_student.password'})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json(), {'values': []})

        # 7. Valid whitelisted column -> 200 OK with distinct non-empty values
        # Insert a few mock records in the "school" table for test verification using Django ORM
        from studata.models import School
        School.objects.all().delete()
        School.objects.create(dw_schoolkey=1, state_id='SCH01', school_name='Test School 1', school_group='Group A')
        School.objects.create(dw_schoolkey=2, state_id='SCH02', school_name='Test School 2', school_group='Group A')
        School.objects.create(dw_schoolkey=3, state_id='SCH03', school_name='Test School 3', school_group='')

        response = self.client.get(url, {'column': 'school.school_group'})
        self.assertEqual(response.status_code, 200)
        res_json = response.json()
        self.assertEqual(res_json, {'values': ['Group A']})

    def test_chart_preview_variation(self):
        """Tests that api_chart_preview endpoint handles variation parameter correctly."""
        from django.urls import reverse
        dataset = Dataset.objects.create(
            name='Test Dataset',
            base_table='current_student',
            columns=['current_student.grade', 'current_student.ssid'],
            owner=self.user
        )
        url = reverse('dataviz:api_chart_preview')
        
        # Log in as superuser to access
        admin_user = User.objects.create_superuser(
            username='admin_preview_test',
            email='admin_preview_test@example.com',
            password='password123',
            name='Admin Preview Test'
        )
        self.client.force_login(admin_user)
        
        from studata.models import CurrentStudent
        CurrentStudent.objects.all().delete()
        CurrentStudent.objects.create(
            stu_annual_key='key1',
            ssid='s1',
            student_id='id1',
            grade='12',
            school_year='2026'
        )
        
        response = self.client.get(url, {
            'source_type': 'dataset',
            'source_id': dataset.pk,
            'chart_type': 'bar',
            'x_col': 'current_student.grade',
            'y_col': 'current_student.ssid',
            'aggregation': 'count',
            'variation': 'horizontal'
        })
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data['xAxis']['type'], 'value')
        self.assertEqual(data['yAxis']['type'], 'category')

    def test_chart_builder_queries_visibility(self):
        """Tests that chart_builder template context contains shared and public queries."""
        from django.urls import reverse
        from dataexploration.models import SavedQuery, QueryShare
        
        user_a = User.objects.create_user(
            username='user_a',
            email='user_a@example.com',
            password='password123',
            name='User A'
        )
        user_b = User.objects.create_user(
            username='user_b',
            email='user_b@example.com',
            password='password123',
            name='User B',
            is_staff=True
        )
        from users.models import Role
        role, _ = Role.objects.get_or_create(name='Project Admin')
        user_b.roles.add(role)
        
        q_private = SavedQuery.objects.create(
            name='Private Query A',
            query_type='filter',
            owner=user_a,
            privacy='private'
        )
        
        q_public = SavedQuery.objects.create(
            name='Public Query A',
            query_type='filter',
            owner=user_a,
            privacy='public'
        )
        
        q_shared = SavedQuery.objects.create(
            name='Shared Query A',
            query_type='filter',
            owner=user_a,
            privacy='shared'
        )
        QueryShare.objects.create(
            query=q_shared,
            shared_with=user_b,
            shared_by=user_a
        )
        
        self.client.force_login(user_b)
        url = reverse('dataviz:chart_create')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        
        queries_in_context = response.context['queries']
        query_ids = [q.id for q in queries_in_context]
        
        self.assertIn(q_public.id, query_ids)
        self.assertIn(q_shared.id, query_ids)
        self.assertNotIn(q_private.id, query_ids)


