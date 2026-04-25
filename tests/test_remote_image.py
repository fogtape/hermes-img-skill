import argparse
import importlib.util
import unittest
from pathlib import Path

SCRIPT_PATH = Path('/home/hermesdev/.hermes/skills/img/scripts/remote_image.py')
spec = importlib.util.spec_from_file_location('remote_image', SCRIPT_PATH)
remote_image = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(remote_image)


class ResolveRequestTests(unittest.TestCase):
    def make_args(self, **overrides):
        base = dict(
            mode='',
            prompt='',
            input=[],
            model='',
            transport='',
            profile=remote_image.PROFILE_DEFAULT,
            size='',
            quality='',
            background='',
            n=1,
            best_of=0,
            negative_hints='',
            variant_of='',
            base_url='',
            api_key='',
            timeout=remote_image.DEFAULT_TIMEOUT,
            outdir='',
            archive=False,
            record_run=False,
            compat_fallback=False,
            list_profiles=False,
            list_runs=False,
            list_runs_limit=20,
            cleanup_days=0,
            cleanup_keep=-1,
            cleanup_all=False,
            dry_run=False,
            show_resolved=False,
            debug=False,
            trace_http=False,
            trace_http_dir='',
        )
        base.update(overrides)
        return argparse.Namespace(**base)

    def test_default_edit_request_is_direct_passthrough(self):
        args = self.make_args(
            prompt='13点32改成13点22，顶部两个地方需要改，别改错，其他地方不用改',
            input=['/tmp/example.png'],
        )

        resolved = remote_image._resolve_request(args, 'gpt-image-2')

        self.assertEqual(resolved['mode'], 'edit')
        self.assertEqual(resolved['profile'], remote_image.PROFILE_DEFAULT)
        self.assertEqual(resolved['prompt'], args.prompt)
        self.assertFalse(resolved['prompt_augmented'])
        self.assertEqual(resolved['size'], '')
        self.assertEqual(resolved['quality'], '')
        self.assertEqual(resolved['n'], 1)

    def test_localized_fix_without_explicit_profile_stays_direct(self):
        args = self.make_args(
            mode='localized-fix',
            prompt='只把 13:32 改成 13:22，别的不要动',
            input=['/tmp/example.png'],
        )

        resolved = remote_image._resolve_request(args, 'gpt-image-2')

        self.assertEqual(resolved['mode'], 'localized-fix')
        self.assertEqual(resolved['profile'], remote_image.PROFILE_DEFAULT)
        self.assertEqual(resolved['prompt'], args.prompt)
        self.assertFalse(resolved['prompt_augmented'])
        self.assertEqual(resolved['size'], '')
        self.assertEqual(resolved['quality'], '')

    def test_explicit_profile_still_applies_profile_defaults(self):
        args = self.make_args(
            prompt='做一张科技海报',
            profile='official-like',
        )

        resolved = remote_image._resolve_request(args, 'gpt-image-2')

        self.assertEqual(resolved['profile'], 'official-like')
        self.assertTrue(resolved['prompt_augmented'])
        self.assertIn('clean composition', resolved['prompt'])
        self.assertEqual(resolved['quality'], 'high')


if __name__ == '__main__':
    unittest.main()
