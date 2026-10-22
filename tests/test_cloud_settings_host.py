"""Offline adapter settings schema and host re-review contracts."""
import unittest
from unittest import mock
import test_cloud_host
from cloud_capabilities import validate_payload
class SettingsHostTests(unittest.TestCase):
 def test_group_name_only_adapter_extension(self):
  p={'title':'Synthetic','content':'Body','images':['/tmp/synthetic.png'],'group_chat':'Observed test group','is_original':False,'activity':'Observed test activity'}
  self.assertEqual(validate_payload('publish_content',p),p)
  for group in ('',[], ' '*3):
   with self.assertRaises(ValueError):validate_payload('publish_content',{**p,'group_chat':group})
 def test_preview_first_rechecks_current_content_review(self):
  f=test_cloud_host.CloudHostTests();f.setUp()
  try:
   h=f.host;payload={'run_id':'synthetic','title':'Exact','content':'Exact'}
   with mock.patch.object(h,'_review_payload',return_value={'payload':payload}) as review,mock.patch.object(h.actions,'preview_unsubmitted_publication_settings',return_value={'binding_digest':'new'}) as preview:
    result=h.call('preview_cloud_publication_settings_revision',{'operation_id':'synthetic','payload':payload,'user_source':'new explicit user instruction'})
    review.assert_called_once_with('publish_content',payload,'new explicit user instruction')
    preview.assert_called_once_with('synthetic',payload,'new explicit user instruction')
    self.assertEqual(result['binding_digest'],'new')
  finally:f.tearDown()
