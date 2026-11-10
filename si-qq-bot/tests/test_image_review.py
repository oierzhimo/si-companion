import io
import unittest
from unittest.mock import Mock
from PIL import Image
from outbound_images import image_segment
from image_review import review_image


def segment():
    f=io.BytesIO();Image.new('RGB',(16,16),'red').save(f,'PNG')
    return image_segment(f.getvalue())


class ReviewTests(unittest.TestCase):
    def test_duplicate_objects_prevent_success(self):
        model=Mock(return_value='{"matches":false,"problems":["要求一个杯子，画面有两个杯子"]}')
        result=review_image('一个杯子',segment(),model,('e','m',[],'k'))
        self.assertFalse(result.matches)
        messages=model.call_args.args[2]
        self.assertEqual(messages[-1]['content'][1]['type'],'image_url')

    def test_invalid_or_contradictory_reviews_never_pass(self):
        for raw in ('画好了','{"matches":true,"problems":["多出一个杯子"]}','{"matches":"true","problems":[]}'):
            with self.subTest(raw=raw):
                self.assertFalse(review_image('杯子',segment(),lambda *args:raw,('e','m',[],'k')).matches)

    def test_invalid_bitmap_never_reaches_model(self):
        model=Mock(side_effect=AssertionError())
        self.assertFalse(review_image('杯子',{'type':'image','data':{'file':'base64://bad'}},model,()).matches)
        model.assert_not_called()
