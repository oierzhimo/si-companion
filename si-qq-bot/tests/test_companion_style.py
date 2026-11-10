import copy,json,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from companion_style import demonstrate,grounded_recall_reply,task_context
from group_core import GROUP_SYSTEM_MEMBER

class StyleContextTests(unittest.TestCase):
    def test_task_mode_keeps_scope_and_does_not_turn_document_into_character_speech(self):
        from si_pet.conversation import PERSONA
        original=[{'role':'system','content':PERSONA},{'role':'system','content':GROUP_SYSTEM_MEMBER},
            {'role':'user','content':'帮我写两句会议通知，时间周五下午三点。'}]
        result=task_context(original)
        self.assertEqual(result[1:],original[1:])
        self.assertIn('不自行补充',result[0]['content'])
        self.assertNotIn('画境',result[0]['content'])
        self.assertEqual(original[0]['content'],PERSONA)
    def test_missing_recall_does_not_guess_but_supplied_user_fact_remains_usable(self):
        query={'role':'user','content':'你记得我昨晚吃了什么吗？'}
        self.assertIn('不知道',grounded_recall_reply([query]))
        self.assertIn('不知道',grounded_recall_reply([query,{'role':'assistant','content':'你吃了红烧肉。'},query]))
        self.assertIsNone(grounded_recall_reply([{'role':'user','content':'我昨晚吃面。'},query]))
        self.assertIsNone(grounded_recall_reply([{'role':'user','content':'17乘24是多少？'}]))

    def test_examples_are_transient_and_leave_real_scope_and_question_intact(self):
        original=[{'role':'system','content':GROUP_SYSTEM_MEMBER},{'role':'user','content':'晚上好呀'}]
        before=copy.deepcopy(original);result=demonstrate(original)
        self.assertEqual(original,before);self.assertEqual(result[0],original[0]);self.assertEqual(result[-1],original[-1])
        self.assertTrue(any(x['role']=='system' and '不是你和当前发言者的聊天记录' in x['content'] for x in result))
        self.assertNotIn('已绑定的管理员本人',str(result))

    def test_utility_and_nontext_requests_do_not_get_casual_demonstrations(self):
        for content in ('17乘24等于多少','帮我写一封正式邮件','概括文件内容',[{'type':'image_url'}]):
            messages=[{'role':'system','content':'persona'},{'role':'user','content':content}]
            self.assertEqual(demonstrate(messages),messages)

    def test_personal_recall_evidence_uses_real_user_words_not_assistant_inventions(self):
        messages=[{'role':'system','content':'persona'},
            {'role':'user','content':'我昨晚吃了面。'},
            {'role':'assistant','content':'你昨天吃的是红烧肉，喝了可乐。'},
            {'role':'user','content':'你记得我昨晚吃了什么吗？'}]
        result=demonstrate(messages)
        evidence=[x['content'] for x in result if x['role']=='user' and x['content'].startswith('可核对的先前用户原话')]
        self.assertEqual(len(evidence),1)
        self.assertIn('我昨晚吃了面',evidence[0]);self.assertNotIn('红烧肉',evidence[0])
        self.assertNotIn('可乐',evidence[0]);self.assertEqual(result[-1],messages[-1])

if __name__=='__main__':unittest.main()
