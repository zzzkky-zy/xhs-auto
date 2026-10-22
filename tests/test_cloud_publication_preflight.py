import copy
import unittest
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from cloud_publication_preflight import validate_publication_preflight as raw_validate

def fixture_expectations(binding):
 return {'template_name':binding['payload'].get('topic_template'),'template_topics':['actual observed topic'],'template_evidence_ref':'SYNTHETIC template source',
         'activity_name':binding['payload'].get('activity'),'activity_topics':[],'activity_evidence_ref':'SYNTHETIC activity source' if binding['payload'].get('activity') else None}

def validate(binding,context,report,now):
 return raw_validate(binding,context,report,now,fixture_expectations(binding))
class PublicationPreflightTests(unittest.TestCase):
 def setUp(self):
  self.binding={'operation':'publish_content','expected_account':'fixture','payload':{'title':'Title','content':'Exact CTA body','topic_template':'answereq','is_original':True}}
  self.report={'expected_account':'fixture','browser_context':'browser','observed_at':'2026-10-20T00:00:00Z','evidence_ref':'fixture-ui','browser_call_succeeded':True,'visible_title':'Title','visible_body':'Exact CTA body','publish_enabled':True,'topic_template_name':'answereq','topic_template_applied':True,'visible_topics':['actual observed topic'],'visible_template_topics':['actual observed topic'],'visible_activity_topics':[],'original_enabled':True,'optional_declaration_selected':False,'mandatory_declaration':{'required':False,'truthfully_completed':False,'evidence_ref':'fixture-no-required-declaration'}}
  self.now=1792454400
 def test_valid(self):self.assertEqual(validate(self.binding,'browser',self.report,self.now)['status'],'validated_host_report')
 def test_wrong_or_unobserved_fields_fail(self):
  for key,value in [('expected_account','other'),('browser_context','other'),('visible_body','altered'),('visible_title','altered'),('topic_template_name','wrong'),('topic_template_applied',False),('original_enabled',False),('optional_declaration_selected',True),('visible_topics',[]),('browser_call_succeeded',False),('publish_enabled',False)]:
   with self.subTest(key=key):
    report=copy.deepcopy(self.report);report[key]=value
    with self.assertRaises(ValueError):validate(self.binding,'browser',report,self.now)
 def test_stale(self):
  with self.assertRaisesRegex(ValueError,'stale'):validate(self.binding,'browser',self.report,self.now+121)
 def test_mandatory_declaration_cannot_be_lied_about_or_ignored(self):
  self.report['mandatory_declaration']['required']=True
  with self.assertRaises(ValueError):validate(self.binding,'browser',self.report,self.now)
  self.report['mandatory_declaration']['truthfully_completed']=True
  validate(self.binding,'browser',self.report,self.now)
 def test_original_false_requires_observed_false(self):
  self.binding['payload']['is_original']=False
  with self.assertRaises(ValueError):validate(self.binding,'browser',self.report,self.now)
  self.report['original_enabled']=False
  self.assertEqual(validate(self.binding,'browser',self.report,self.now)['status'],'validated_host_report')
 def test_original_requires_explicit_booleans(self):
  for requested in (True,False):
   for observed in (None,0,1,'true','false'):
    with self.subTest(requested=requested,observed=observed):
     self.binding['payload']['is_original']=requested
     self.report['original_enabled']=observed
     with self.assertRaises(ValueError):validate(self.binding,'browser',self.report,self.now)
  for requested in (None,0,1,'true','false'):
   with self.subTest(requested=requested):
    self.binding['payload']['is_original']=requested
    self.report['original_enabled']=requested
    with self.assertRaises(ValueError):validate(self.binding,'browser',self.report,self.now)
  del self.binding['payload']['is_original']
  self.report['original_enabled']=False
  with self.assertRaises(ValueError):validate(self.binding,'browser',self.report,self.now)
 def test_original_observation_cannot_be_omitted(self):
  self.binding['payload']['is_original']=False
  del self.report['original_enabled']
  with self.assertRaisesRegex(ValueError,'exact observation fields'):validate(self.binding,'browser',self.report,self.now)
 def test_optional_selections_exact_readback(self):
  for selections in ({'group_chat':'Current group'},{'activity':'Current activity'},{'group_chat':'Current group','activity':'Current activity'}):
   with self.subTest(selections=selections):
    binding=copy.deepcopy(self.binding)
    report=copy.deepcopy(self.report)
    binding['payload'].update(selections,is_original=False)
    report['original_enabled']=False
    report.update({'selected_'+field:value for field,value in selections.items()})
    self.assertEqual(validate(binding,'browser',report,self.now)['status'],'validated_host_report')
 def test_optional_selections_must_be_observed_exactly(self):
  for field in ('group_chat','activity'):
   with self.subTest(field=field):
    binding=copy.deepcopy(self.binding)
    binding['payload'][field]='Exact current name'
    with self.assertRaisesRegex(ValueError,'exact observation fields'):validate(binding,'browser',self.report,self.now)
    for observed in (None,False,'','Other name','Exact current name ','exact current name'):
     with self.subTest(observed=observed):
      report=copy.deepcopy(self.report)
      report['selected_'+field]=observed
      with self.assertRaises(ValueError):validate(binding,'browser',report,self.now)
 def test_optional_selection_names_must_be_nonempty_strings(self):
  for field in ('group_chat','activity'):
   for value in (None,False,0,[],{},'','  '):
    with self.subTest(field=field,value=value):
     binding=copy.deepcopy(self.binding)
     report=copy.deepcopy(self.report)
     binding['payload'][field]=value
     report['selected_'+field]=value
     with self.assertRaises(ValueError):validate(binding,'browser',report,self.now)
 def test_unrequested_selection_observation_is_not_part_of_schema(self):
  for field in ('group_chat','activity'):
   with self.subTest(field=field):
    report=copy.deepcopy(self.report)
    report['selected_'+field]='Unrequested name'
    with self.assertRaisesRegex(ValueError,'exact observation fields'):validate(self.binding,'browser',report,self.now)

 def test_scheduled_candidate_requires_actual_control_readback(self):
  self.binding['payload']['schedule_at']='2026-10-21T17:30:00+08:00'
  with self.assertRaises(ValueError):validate(self.binding,'browser',self.report,self.now)
  self.report.update(visible_schedule_at=self.binding['payload']['schedule_at'],schedule_error_absent=True)
  validate(self.binding,'browser',self.report,self.now)
  self.report['schedule_error_absent']=False
  with self.assertRaises(ValueError):validate(self.binding,'browser',self.report,self.now)
 def test_updated_settings_preserve_existing_gates(self):
  self.binding['payload'].update(is_original=False,group_chat='Current group',activity='Current activity',schedule_at='2026-10-21T17:30:00+08:00')
  self.report.update(original_enabled=False,selected_group_chat='Current group',selected_activity='Current activity',visible_schedule_at=self.binding['payload']['schedule_at'],schedule_error_absent=True)
  validate(self.binding,'browser',self.report,self.now)
  for key,value in [('expected_account','other'),('browser_context','other'),('visible_body','altered'),('visible_title','altered'),('topic_template_name','wrong'),('topic_template_applied',False),('optional_declaration_selected',True),('visible_topics',[]),('browser_call_succeeded',False),('publish_enabled',False),('visible_schedule_at','2026-10-21T17:31:00+08:00'),('schedule_error_absent',False),('observed_at','2026-10-19T23:57:59Z')]:
   with self.subTest(key=key):
    report=copy.deepcopy(self.report);report[key]=value
    with self.assertRaises(ValueError):validate(self.binding,'browser',report,self.now)

class TopicSeparationTests(PublicationPreflightTests):
 def test_missing_baseline_specific_error(self):
  with self.assertRaisesRegex(ValueError,'TOPICS_UNVERIFIED.*first read actual'):
   raw_validate(self.binding,'browser',self.report,self.now)
 def test_nonempty_wrong_chips_rejected(self):
  self.report['visible_topics']=['unrelated but nonempty']
  with self.assertRaisesRegex(ValueError,'combined visible topics'):validate(self.binding,'browser',self.report,self.now)
 def test_activity_chip_is_preserved_not_removed(self):
  self.binding['payload']['activity']='Activity'
  self.report.update(selected_activity='Activity',visible_activity_topics=['actual activity topic'],visible_topics=['actual observed topic','actual activity topic'])
  expected=fixture_expectations(self.binding);expected['activity_topics']=['actual activity topic']
  before=copy.deepcopy((self.binding,self.report,expected))
  raw_validate(self.binding,'browser',self.report,self.now,expected)
  self.assertEqual(before,(self.binding,self.report,expected))
  self.report['visible_topics'].pop()
  with self.assertRaises(ValueError):raw_validate(self.binding,'browser',self.report,self.now,expected)
 def test_correct_union_wrong_source_partition_rejected(self):
  self.report.update(visible_template_topics=[],visible_activity_topics=['actual observed topic'])
  with self.assertRaises(ValueError):validate(self.binding,'browser',self.report,self.now)
