import unittest
from unittest.mock import Mock
from image_prompt import compile_prompt, PromptError


class PromptTests(unittest.TestCase):
    def test_english_description_does_not_need_language_model(self):
        model=Mock(side_effect=AssertionError())
        text='one red cup, blue table, no people'
        self.assertEqual(compile_prompt(text,model,()),text)
        model.assert_not_called()

    def test_only_visual_request_is_forwarded_without_chat_history(self):
        model=Mock(return_value='{"positive":"one red cup on a blue table, white background, no people"}')
        result=compile_prompt('一个红杯子在蓝桌上，白背景，没有人',model,('endpoint','model',[{'role':'user','content':'private history'}],'key'))
        self.assertIn('no people',result)
        sent=model.call_args.args[2]
        self.assertEqual(len(sent),2)
        self.assertNotIn('private history',str(sent))

    def test_invalid_translation_fails_before_generation(self):
        for raw in ('画好了。','{"positive":"红杯子"}','{"positive":"cup","path":"C:/secret"}','{"positive":12}'):
            with self.subTest(raw=raw),self.assertRaises(PromptError):
                compile_prompt('红杯子',lambda *args:raw,('e','m',[],'k'))
