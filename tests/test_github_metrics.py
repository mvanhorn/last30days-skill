"""Offline regression coverage for GitHub collector units and rendered footers."""
import copy
import io
import unittest
from unittest.mock import patch
import urllib.parse

from lib import github, normalize, render, schema

START, END = '2026-10-01', '2026-10-25'

def fake_json(url, **kwargs):
    """Inert HTTP boundary: real collectors/enrichers and normalization still run."""
    parsed = urllib.parse.urlsplit(url)
    if '/events/public' in parsed.path:
        return [{'id': 'event-1', 'type': 'PushEvent', 'actor': {'login': 'alice'},
                 'repo': {'name': 'alice/own'}, 'created_at': '2026-10-20T12:00:00Z'}]
    if '/users/alice/repos' in parsed.path:
        return [{'full_name': 'alice/own', 'stargazers_count': 123,
                 'open_issues_count': 17, 'description': 'Own repo', 'fork': False}]
    if parsed.path == '/search/issues':
        query = urllib.parse.parse_qs(parsed.query).get('q', [''])[0]
        if 'author:alice' in query:
            return {'total_count': 3 if 'is:merged' in query else 8,
                    'items': [{'html_url': 'https://github.com/other/ext/pull/1'}] if 'is:merged' in query else []}
        return {'total_count': 1, 'items': [{'title': 'Real issue excerpt', 'comments': 9,
                'reactions': {'total_count': 5}, 'html_url': 'https://github.com/alice/own/issues/1'}]}
    if parsed.path.endswith('/releases'):
        return []
    if parsed.path.startswith('/repos/'):
        return {'stargazers_count': 123, 'open_issues_count': 17,
                'description': 'Repo description', 'language': 'Python', 'forks_count': 4}
    raise AssertionError('Unexpected HTTP endpoint: ' + url)

class CollectorMetrics(unittest.TestCase):
    def setUp(self):
        self.http = patch.object(github, '_fetch_json', side_effect=fake_json)
        self.readme = patch.object(github.http, 'open_request', side_effect=lambda *a, **k: io.BytesIO(b'Real README fixture'))
        self.http.start(); self.readme.start()
        self.addCleanup(self.http.stop); self.addCleanup(self.readme.stop)

    def normalized(self, item):
        return normalize._normalize_github('github', item, 0, START, END)

    def assert_counter(self, item, key, count):
        self.assertEqual(item['engagement'].get(key), count)
        self.assertNotIn('comments', item['engagement'])
        self.assertEqual(item['metadata'].get('comment_count', 0), 0)
        normalized = self.normalized(item)
        self.assertEqual(normalized.engagement[key], count)
        self.assertEqual(normalized.metadata['labels'], item['metadata']['labels'])

    def test_recent_push_uses_open_issues(self):
        items = github._person_recent_pushes('alice', START, END, github.PERSON_DEPTH_LIMITS['default'], 'inert-token')
        self.assertEqual(len(items), 1)
        self.assert_counter(items[0], 'open_issues', 17)
        self.assertEqual(items[0]['author'], 'alice')
        self.assertEqual(items[0]['date'], '2026-10-20')
        self.assertEqual(items[0]['metadata']['event_id'], 'event-1')

    def test_person_own_repo_uses_open_issues(self):
        items = github.search_github_person('alice', START, END, token='inert-token')
        item = next(x for x in items if 'own-repo' in x['metadata']['labels'])
        self.assert_counter(item, 'open_issues', 17)
        self.assertIn('Real issue excerpt', item['snippet'])
        self.assertEqual(item['author'], 'alice')

    def test_person_external_repo_uses_contributed_prs(self):
        items = github.search_github_person('alice', START, END, token='inert-token')
        item = next(x for x in items if 'external-repo' in x['metadata']['labels'])
        self.assert_counter(item, 'contributed_prs', 1)
        self.assertEqual(item['engagement']['stars'], 123)
        self.assertNotIn('merged_prs', item['engagement'])

    def test_person_velocity_uses_total_prs(self):
        items = github.search_github_person('alice', START, END, token='inert-token')
        item = next(x for x in items if 'velocity' in x['metadata']['labels'])
        self.assert_counter(item, 'total_prs', 8)
        self.assertEqual(item['engagement']['merged_prs'], 3)

    def test_project_open_issues_are_not_comments(self):
        items = github.search_github_project(['alice/own'], START, END, token='inert-token')
        self.assertEqual(len(items), 1)
        self.assert_counter(items[0], 'open_issues', 17)
        self.assertEqual(items[0]['engagement']['stars'], 123)
        self.assertIn('Real issue excerpt', items[0]['snippet'])
        self.assertIn('9 comments', items[0]['snippet'])
        self.assertEqual(items[0]['date'], END)
        self.assertEqual(items[0]['author'], 'alice')
        self.assertTrue(self.normalized(items[0]).metadata['date_is_snapshot'])

def metric_report(items, **other_sources):
    return schema.Report(
        topic='GitHub metrics', range_from=START, range_to=END, generated_at=END+'T12:00:00Z',
        provider_runtime=schema.ProviderRuntime(reasoning_provider='gemini', planner_model='inert', rerank_model='inert'),
        query_plan=schema.QueryPlan(intent='general', freshness_mode='balanced_recent', cluster_mode='story',
            raw_topic='GitHub metrics', subqueries=[schema.SubQuery(label='primary', search_query='metrics', ranking_query='metrics', sources=['github'])], source_weights={'github': 1.0}),
        clusters=[], ranked_candidates=[], items_by_source={'github': items, **other_sources}, errors_by_source={})

def metric_item(engagement, labels=None, url='https://github.com/alice/own'):
    return normalize._normalize_github('github', {'id': 'GH1', 'title': 'Metrics', 'url': url,
        'date': END, 'engagement': engagement, 'metadata': {'labels': labels or []}}, 0, START, END)

class FooterMetrics(unittest.TestCase):
    def test_total_prs_visible_in_all_renderer_footers(self):
        report = metric_report([metric_item({'open_issues': 0, 'total_prs': 8})])
        before = copy.deepcopy(report)
        expected = '🐙 GitHub: 1 item │ 8 total PRs'
        self.assertEqual(render._build_source_footer_lines(report), [expected])
        for text in [render.render_compact(report), render.render_for_html(report),
                     render.render_comparison_multi([('Alice', report)]),
                     render.render_for_html_comparison([('Alice', report)])]:
            self.assertIn(expected, text)
        self.assertEqual(report, before)

    def test_contributed_prs_visible_in_all_renderer_footers(self):
        report = metric_report([metric_item({'open_issues': 0, 'contributed_prs': 2})])
        before = copy.deepcopy(report)
        expected = '🐙 GitHub: 1 item │ 2 contributed PRs'
        self.assertEqual(render._build_source_footer_lines(report), [expected])
        for text in [render.render_compact(report), render.render_for_html(report),
                     render.render_comparison_multi([('Alice', report)]),
                     render.render_for_html_comparison([('Alice', report)])]:
            self.assertIn(expected, text)
        self.assertEqual(report, before)

    def test_pr_volume_footer_matrix_across_renderers(self):
        cases = [
            ('zero-open-inventory', [metric_item({'open_issues': 0, 'total_prs': 8, 'contributed_prs': 2})],
             '8 total PRs │ 2 contributed PRs'),
            ('typed', [metric_item({'stars': 123, 'merged_prs': 3, 'reactions': 5,
                                   'open_issues': 17, 'total_prs': 8, 'contributed_prs': 2})],
             '123 stars │ 3 merged │ 5 reactions │ 17 open issues/PRs │ 8 total PRs │ 2 contributed PRs'),
            ('legacy', [metric_item({'comments': 8}, ['velocity'], 'https://github.com/alice/?tab=overview'),
                        metric_item({'comments': 2}, ['external-repo'], 'https://github.com/other/ext/')],
             '8 total PRs │ 2 contributed PRs'),
            ('typed-precedence', [metric_item({'total_prs': 8, 'comments': 99}, ['velocity'], 'https://github.com/alice'),
                                  metric_item({'contributed_prs': 2, 'comments': 99}, ['external-repo'])],
             '8 total PRs │ 2 contributed PRs'),
            ('typed-zero', [metric_item({'total_prs': 0, 'comments': 99}, ['velocity'], 'https://github.com/alice'),
                            metric_item({'contributed_prs': 0, 'comments': 99}, ['external-repo'])], ''),
            ('ambiguous-alias', [metric_item({'comments': 99}, ['bug']),
                                 metric_item({'comments': 99}, ['velocity'])], ''),
            ('formatted-counts', [metric_item({'total_prs': 1008, 'contributed_prs': 2002})],
             '1,008 total PRs │ 2,002 contributed PRs'),
        ]
        for name, items, stats in cases:
            # Issue and PR comments must remain genuine discussion counts even
            # when their labels look like legacy repository-summary provenance.
            items += [metric_item({'comments': 9}, ['velocity'], 'https://github.com/alice/own/issues/1'),
                      metric_item({'comments': 4}, ['external-repo'], 'https://github.com/alice/own/pull/2')]
            report = metric_report(items)
            before = copy.deepcopy(report)
            expected = f'🐙 GitHub: {len(items)} items │ ' + (stats + ' │ ' if stats else '') + '13 comments'
            for renderer, text in [
                ('compact', render.render_compact(report)),
                ('html', render.render_for_html(report)),
                ('comparison', render.render_comparison_multi([('Alice', report)])),
                ('html-comparison', render.render_for_html_comparison([('Alice', report)])),
            ]:
                with self.subTest(case=name, renderer=renderer):
                    # The complete emoji-prefixed footer line cannot match a
                    # title, snippet, or raw engagement evidence by accident.
                    self.assertEqual(render._build_source_footer_lines(report), [expected])
                    self.assertIn(expected, text)
                    self.assertEqual(report, before)

    def test_classification_positive_controls(self):
        for label, url, key in [
            ('project-mode', 'https://github.com/alice/own', 'open_issues'),
            ('own-repo', 'https://github.com/alice/own/', 'open_issues'),
            ('recent-push', 'https://github.com/alice/own?q=1', 'open_issues'),
            ('velocity', 'https://github.com/alice/', 'total_prs'),
            ('external-repo', 'https://github.com/other/ext/', 'contributed_prs'),
        ]:
            for typed in [{}, {key: 0}, {key: 4}]:
                with self.subTest(label=label, typed=typed):
                    item = metric_item({'stars': 123, 'merged_prs': 3, 'reactions': 5, 'comments': 17, **typed}, [label], url)
                    before = copy.deepcopy(item)
                    metrics = render._github_footer_engagement(item)
                    self.assertEqual(metrics[key], typed.get(key, 17))
                    self.assertNotIn('comments', metrics)
                    self.assertEqual([metrics[k] for k in ['stars', 'merged_prs', 'reactions']], [123, 3, 5])
                    self.assertEqual(item, before)
        for metadata in [None, [], 'project-mode', {'labels': None}, {'labels': 9}]:
            with self.subTest(metadata=metadata):
                item = metric_item({'comments': 17}); item.metadata = metadata
                self.assertNotIn('comments', render._github_footer_engagement(item))
        for label, url in [('velocity', 'https://github.com/alice/own'), ('project-mode', 'https://github.com/alice')]:
            with self.subTest(label=label, url=url):
                metrics = render._github_footer_engagement(metric_item({'comments': 17}, [label], url))
                self.assertNotIn('open_issues', metrics)
                self.assertNotIn('total_prs', metrics)
        item = normalize._normalize_github('github', {'id': 'GHtyped', 'url': 'https://github.com/alice/own',
            'title': 'typed', 'engagement': {'open_issues': 0, 'total_prs': 8, 'contributed_prs': 2},
            'metadata': {'open_issues': 99, 'total_prs': 99, 'contributed_prs': 99}}, 0, START, END)
        self.assertEqual(item.engagement, {'open_issues': 0, 'total_prs': 8, 'contributed_prs': 2})
        self.assertFalse(any(key in item.metadata for key in item.engagement))

    def test_legacy_summary_comment_alias_is_classified_per_item(self):
        cases = []
        for label in ['project-mode', 'own-repo', 'recent-push']:
            for suffix in ['', '/', '/?tab=readme', '?tab=readme']:
                cases.append((label+suffix, 'https://github.com/alice/own'+suffix,
                              {'labels': [label]}, {}, 17, 0))
        cases += [
            ('velocity', 'https://github.com/alice/?tab=overview', {'labels': ['velocity']}, {}, 0, 0),
            ('external', 'https://github.com/other/ext', {'labels': ['external-repo']}, {}, 0, 0),
            ('typed-zero', 'https://github.com/alice/own', {'labels': ['project-mode']}, {'open_issues': 0}, 0, 0),
            ('typed-pr-zero', 'https://github.com/alice', {'labels': ['velocity']}, {'total_prs': 0}, 0, 0),
            ('typed-contrib-zero', 'https://github.com/other/ext', {'labels': ['external-repo']}, {'contributed_prs': 0}, 0, 0),
            ('typed-precedence', 'https://github.com/alice/own', {'labels': ['own-repo']}, {'open_issues': 4}, 4, 0),
            ('ambiguous', 'https://github.com/alice/own', {'labels': ['bug']}, {}, 0, 0),
            ('malformed-metadata', 'https://github.com/alice/own', None, {}, 0, 0),
            ('malformed-label-string', 'https://github.com/alice/own', {'labels': 'project-mode'}, {}, 0, 0),
            ('malformed-label-dict', 'https://github.com/alice/own', {'labels': {'project-mode': True}}, {}, 0, 0),
        ]
        for url in ['https://github.com.evil/alice/own', 'https://evilgithub.com/alice/own',
                    'https://github.com@evil.test/alice/own', 'https://user@github.com/alice/own',
                    'https://github.com:443/alice/own', 'ftp://github.com/alice/own',
                    'github.com/alice/own', 'https://github.com/alice/own/tree/main',
                    'https://github.com/alice//own', 'https://github.com/alice/own/extra']:
            cases.append(('url-guard:'+url, url, {'labels': ['project-mode']}, {}, 0, 17))
        for label in ['project-mode', 'own-repo', 'recent-push', 'velocity', 'external-repo']:
            for kind in ['issues', 'pull']:
                cases.append((label+kind, 'https://github.com/alice/own/'+kind+'/7/?q=1',
                              {'labels': [label], 'is_pr': False}, {}, 0, 17))
        for name, url, metadata, typed, open_count, comment_count in cases:
            with self.subTest(case=name):
                item = metric_item({'stars': 123, 'reactions': 5, 'merged_prs': 3, 'comments': 17, **typed}, url=url)
                item.metadata = metadata
                # Title words and is_pr=False are not summary provenance.
                item.title = 'Repository total PRs project-mode'
                real = metric_item({'comments': 9}, ['velocity'], 'https://github.com/alice/own/issues/1')
                report = metric_report([item, real]); before = copy.deepcopy(report)
                line = render._build_source_footer_lines(report)[0]
                expected = '🐙 GitHub: 2 items │ 123 stars │ 3 merged │ 5 reactions'
                if open_count: expected += f' │ {open_count} open issues/PRs'
                if name == 'velocity': expected += ' │ 17 total PRs'
                if name == 'external': expected += ' │ 17 contributed PRs'
                expected += f' │ {comment_count + 9} comments'
                self.assertEqual(line, expected)
                self.assertEqual(report, before)

    def test_typed_open_counter_is_separate_from_real_comments(self):
        report = metric_report([metric_item({'stars': 123, 'open_issues': 17}, ['project-mode']),
            metric_item({'comments': 9, 'reactions': 5}, ['project-mode'], 'https://github.com/alice/own/issues/1')])
        before = copy.deepcopy(report)
        line = render._build_source_footer_lines(report)[0]
        self.assertEqual(line, '🐙 GitHub: 2 items │ 123 stars │ 5 reactions │ 17 open issues/PRs │ 9 comments')
        # Full is a raw evidence renderer: it does not call the shared footer.
        self.assertIn('## All Items by Source', render.render_full(report))
        for text in [render.render_compact(report), render.render_for_html(report),
                     render.render_comparison_multi([('Alice', report)]), render.render_for_html_comparison([('Alice', report)])]:
            self.assertIn('17 open issues/PRs', text)
            self.assertIn('9 comments', text)
        self.assertEqual(report, before)
