import json
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from agents.verifier import CitationVerifier
from agents.tools.text_revision import locate, apply_replacements
from agents.tools.json_response import parse_json

class VerificationTests(unittest.TestCase):
    evidence=[{'index':1,'text':'Evidence'}]
    issue={'claims':[{'quote':'Bad claim.','citations':[1],'verdict':'unsupported','reason':'Not supported'}]}

    def test_revision_rechecked(self):
        llm=Mock(side_effect=[json.dumps(self.issue),json.dumps({'replacements':[{'quote':'Bad claim.','new':'Supported claim.','citation':[1]}]}),'{"claims":[]}'])
        text,record=CitationVerifier(llm,min_chars=1).execute('Bad claim.',self.evidence)
        self.assertEqual(text,'Supported claim.')
        self.assertEqual(record['status'],'rewritten')
        self.assertEqual(llm.call_count,3)

    def test_regeneration_and_recheck(self):
        llm=Mock(side_effect=[json.dumps(self.issue),'{"replacements":[]}',json.dumps(self.issue),'{"claims":[]}'])
        regenerate=Mock(return_value='New report.')
        text,record=CitationVerifier(llm).execute('Bad claim.',self.evidence,max_rounds=1,regenerate=regenerate)
        self.assertEqual(text,'New report.')
        self.assertTrue(record['regenerated'])
        regenerate.assert_called_once()

    def test_unresolved_delivers_with_warning(self):
        # 校验不再阻断交付：修订不收敛时交回正文并标 partial，让流程跑完。
        llm=Mock(side_effect=[json.dumps(self.issue),'{"replacements":[]}',json.dumps(self.issue)])
        text,record=CitationVerifier(llm).execute('Bad claim.',self.evidence,max_rounds=1)
        self.assertEqual(text,'Bad claim.')
        self.assertEqual(record['status'],'partial')
        self.assertEqual(len(record['remaining_issues']),1)

    def test_text_tools(self):
        self.assertEqual(locate('A B\nC','ABC'),(0,5))
        text,applied,deleted=apply_replacements('First. Second.',[{'quote':'First.','new':''}])
        self.assertEqual(text,'Second.')
        self.assertEqual(deleted,1)
        self.assertEqual(len(applied),1)
        self.assertEqual(parse_json('```json\n{"claims": []}\n```'),{'claims':[]})

if __name__=='__main__':unittest.main()
