import logging
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from si_pet.app import configure_logging


class StartupLoggingTests(unittest.TestCase):
    def test_missing_portable_log_directory_falls_back_to_user_data(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            blocked=root/'missing'/'app.log'
            fallback=root/'user'/'SiDesktopPet'/'app.log'
            logger=logging.getLogger()
            old_handlers=list(logger.handlers)
            original_handler=logging.FileHandler
            def file_handler(path,**kwargs):
                if Path(path)==blocked:
                    raise FileNotFoundError('portable folder vanished')
                return original_handler(path,**kwargs)
            try:
                with patch('si_pet.app.data_root',return_value=blocked.parent), \
                     patch.dict('si_pet.app.os.environ',{'LOCALAPPDATA':str(root/'user')}), \
                     patch('si_pet.app.logging.FileHandler',side_effect=file_handler):
                    path=configure_logging()
                self.assertEqual(path,fallback)
                logging.info('startup fallback works')
                self.assertIn('startup fallback works',fallback.read_text(encoding='utf-8'))
            finally:
                for handler in logger.handlers:
                    if handler not in old_handlers:
                        logger.removeHandler(handler)
                        handler.close()


if __name__=='__main__':
    unittest.main()
