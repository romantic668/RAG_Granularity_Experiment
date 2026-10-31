import io
import json
import os
import subprocess
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import test_study as base
from ragstudy.api import APIClient, APIError
from ragstudy.core import VERIFY_PROMPT, VERIFY_SCHEMA, write_json
from ragstudy.http_transport import error_detail


class CurlPipelineTests(base.APIPipelineTests):
    """Repeat pipeline invariants through an actual curl process and local server."""
    def setUp(self):
        super().setUp()
        self.cfg['http_transport'] = 'curl'
        write_json(self.cfg_path, self.cfg)

    def test_secret_is_in_stdin_and_unicode_body_survives(self):
        original = subprocess.run
        calls = []

        def checked(command, **kwargs):
            calls.append(command)
            self.assertNotIn('local-unit-test-only', ' '.join(command))
            self.assertIn(b'local-unit-test-only', kwargs['input'])
            self.assertNotIn('--insecure', command)
            self.assertEqual(command[1], '--disable')
            return original(command, **kwargs)

        client = APIClient(self.cfg, self.root/'curl_secrecy', 1, 2)
        with patch('ragstudy.http_transport.subprocess.run', side_effect=checked):
            result, _ = client.call('verify', 'unicode', VERIFY_PROMPT,
                                    {'target_unit': '€ arrives on Monday.'}, VERIFY_SCHEMA)
        self.assertEqual(result['status'], 'supported')
        self.assertEqual(len(calls), 1)
        self.assertEqual(json.loads(self.requests[-1]['messages'][1]['content'])['target_unit'], '€ arrives on Monday.')

    def test_error_detail_is_usable_and_key_redacted(self):
        secret = 'sk-local-private-sentinel'
        error = HTTPError('https://example.invalid', 400, 'bad', {'x-request-id':'req_mock'},
                          io.BytesIO(json.dumps({'error': {'message':'Unsupported value '+secret,
                                                          'param':'reasoning_effort', 'code':'unsupported_value'}}).encode()))
        detail = error_detail(error, secret)
        self.assertEqual(detail['param'], 'reasoning_effort')
        self.assertEqual(detail['request_id'], 'req_mock')
        self.assertNotIn(secret, json.dumps(detail))

    def test_error_details_reach_console_exception_and_local_log(self):
        error = HTTPError('https://example.invalid', 400, 'bad', {'x-request-id':'req_mock'},
                          io.BytesIO(b'{"error":{"message":"This service tier is not permitted","param":"service_tier","code":"invalid_service_tier"}}'))
        client = APIClient(self.cfg, self.root/'error_log', 1, 2)
        with patch('ragstudy.api.curl_urlopen', side_effect=error):
            with self.assertRaisesRegex(APIError, 'service_tier'):
                client.call('verify', 'failed', VERIFY_PROMPT, {'target_unit':'A'}, VERIFY_SCHEMA)
        event = json.loads((self.root/'error_log'/'calls.jsonl').read_text())
        self.assertFalse(event['ok'])
        self.assertEqual(event['error_detail']['request_id'], 'req_mock')


if __name__ == '__main__':
    import unittest
    unittest.main()
