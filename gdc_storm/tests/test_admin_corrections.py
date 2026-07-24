import threading
import time
from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from gdc_storm.admin_jobs import (
    JOB_TYPE_REEXTRACT,
    get_correction_job,
    reset_correction_job_for_tests,
    start_correction_job,
)


class AdminCorrectionsViewsTest(TestCase):
    def setUp(self):
        reset_correction_job_for_tests()
        self.client = Client()
        self.superuser = User.objects.create_superuser(
            username='admin_corr', password='pass', email='a@example.com'
        )
        self.user = User.objects.create_user(username='user_corr', password='pass')

    def tearDown(self):
        reset_correction_job_for_tests()

    def test_page_requires_superuser(self):
        url = reverse('admin_corrections')
        self.client.login(username='user_corr', password='pass')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 403)

        self.client.login(username='admin_corr', password='pass')
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, 'Scripts de correction')

    def test_run_and_status_require_superuser(self):
        self.client.login(username='user_corr', password='pass')
        run_url = reverse('admin_corrections_run')
        status_url = reverse('admin_corrections_status')
        resp = self.client.post(
            run_url,
            data='{"job_type":"reextract","options":{"dry_run":true}}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 403)
        resp = self.client.get(status_url)
        self.assertEqual(resp.status_code, 403)

    def test_post_starts_dry_run_job_and_status_returns_counters(self):
        self.client.login(username='admin_corr', password='pass')
        run_url = reverse('admin_corrections_run')
        status_url = reverse('admin_corrections_status')

        with patch('gdc_storm.admin_jobs.run_reextract_missions') as mock_run:
            mock_run.return_value = {
                'processed': 3,
                'total': 3,
                'updated': 1,
                'skipped': 2,
                'errors': 0,
            }
            resp = self.client.post(
                run_url,
                data='{"job_type":"reextract","options":{"dry_run":true,"map":"altis"}}',
                content_type='application/json',
            )
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertTrue(data['ok'])
            job_id = data['job']['job_id']
            self.assertEqual(data['job']['job_type'], 'reextract')

            deadline = time.time() + 5
            job = None
            while time.time() < deadline:
                status = self.client.get(status_url, {'job_id': job_id}).json()
                job = status.get('job')
                if job and job['status'] in ('done', 'error'):
                    break
                time.sleep(0.05)

            self.assertIsNotNone(job)
            self.assertEqual(job['status'], 'done')
            self.assertEqual(job['processed'], 3)
            self.assertEqual(job['updated'], 1)
            self.assertEqual(job['skipped'], 2)
            mock_run.assert_called_once()
            kwargs = mock_run.call_args.kwargs
            self.assertTrue(kwargs['dry_run'])
            self.assertEqual(kwargs['map_code'], 'altis')

    def test_concurrent_job_refused(self):
        release = threading.Event()

        def blocking_run(job):
            from gdc_storm import admin_jobs
            with admin_jobs._lock:
                job['status'] = 'running'
                job['started_at'] = time.time()
            release.wait(timeout=5)
            with admin_jobs._lock:
                job['status'] = 'done'
                job['finished_at'] = time.time()

        with patch('gdc_storm.admin_jobs._run_job', side_effect=blocking_run):
            first = start_correction_job(JOB_TYPE_REEXTRACT, {'dry_run': True})
            self.assertTrue(first['ok'])
            second = start_correction_job(JOB_TYPE_REEXTRACT, {'dry_run': True})
            self.assertFalse(second['ok'])
            self.assertIn('déjà en cours', second['error'])

            self.client.login(username='admin_corr', password='pass')
            resp = self.client.post(
                reverse('admin_corrections_run'),
                data='{"job_type":"extract_markers","options":{"dry_run":true}}',
                content_type='application/json',
            )
            self.assertEqual(resp.status_code, 409)
            self.assertFalse(resp.json()['ok'])

            release.set()
            deadline = time.time() + 5
            while time.time() < deadline:
                job = get_correction_job(first['job']['job_id'])
                if job and job['status'] == 'done':
                    break
                time.sleep(0.05)

    def test_invalid_job_type(self):
        self.client.login(username='admin_corr', password='pass')
        resp = self.client.post(
            reverse('admin_corrections_run'),
            data='{"job_type":"nope","options":{}}',
            content_type='application/json',
        )
        self.assertEqual(resp.status_code, 400)
