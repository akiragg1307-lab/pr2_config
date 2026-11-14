"""Модульные тесты визуализатора графа зависимостей Maven."""

import importlib
import sys
import tempfile
import unittest
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent.parent / "src"
sys.path.insert(0, str(SRC_DIR))
main = importlib.import_module("main")

VALID_TEST_CONFIG = """[settings]
package_name = A
repository = {repo}
test_mode = true
ascii_tree = false
"""


class TempDirTestCase(unittest.TestCase):
    """Базовый класс: временный каталог для файлов теста."""

    def setUp(self):
        """Создать временный каталог."""
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        """Удалить временный каталог."""
        self._tmp.cleanup()

    def write(self, name, text):
        """Записать файл во временный каталог и вернуть путь."""
        path = self.tmp / name
        path.write_text(text, encoding="utf-8")
        return path


class TestConfig(TempDirTestCase):
    """Этап 1: чтение и проверка INI-конфигурации."""

    def make_config(self, text=None):
        """Создать конфигурацию и тестовый репозиторий."""
        repo = self.write("repo.txt", "A: B\n")
        text = text or VALID_TEST_CONFIG.format(repo=repo)
        return self.write("config.ini", text)

    def test_valid_config(self):
        """Корректный файл загружается со всеми параметрами."""
        config = main.load_config(self.make_config())
        self.assertEqual(config.package_name, "A")
        self.assertTrue(config.test_mode)
        self.assertFalse(config.ascii_tree)

    def test_missing_file(self):
        """Отсутствующий файл конфигурации — ошибка."""
        with self.assertRaises(main.ConfigError):
            main.load_config(self.tmp / "absent.ini")

    def test_missing_section(self):
        """Файл без секции [settings] — ошибка."""
        with self.assertRaises(main.ConfigError):
            main.load_config(self.make_config("[other]\nkey = 1\n"))

    def test_missing_key(self):
        """Пропущенный параметр — ошибка."""
        text = "[settings]\npackage_name = A\n"
        with self.assertRaises(main.ConfigError):
            main.load_config(self.make_config(text))

    def test_bad_boolean(self):
        """Нелогическое значение test_mode — ошибка."""
        path = self.make_config()
        text = path.read_text().replace("test_mode = true", "test_mode = 5")
        with self.assertRaises(main.ConfigError):
            main.load_config(self.write("config.ini", text))

    def test_bad_test_package_name(self):
        """В тестовом режиме имя пакета — только большие буквы."""
        path = self.make_config()
        text = path.read_text().replace("= A", "= a1")
        with self.assertRaises(main.ConfigError):
            main.load_config(self.write("config.ini", text))

    def test_bad_url(self):
        """В обычном режиме repository должен быть URL."""
        text = (
            "[settings]\npackage_name = org.example:lib\n"
            "repository = repo.example.org\n"
            "test_mode = false\nascii_tree = false\n"
        )
        with self.assertRaises(main.ConfigError):
            main.load_config(self.make_config(text))

    def test_unknown_key(self):
        """Неизвестный параметр — ошибка."""
        text = self.make_config().read_text() + "depth = 3\n"
        with self.assertRaises(main.ConfigError):
            main.load_config(self.write("config.ini", text))

    def test_main_returns_error_code(self):
        """При ошибке конфигурации main возвращает ненулевой код."""
        code = main.main(["-c", str(self.tmp / "absent.ini")])
        self.assertEqual(code, main.EXIT_ERROR)
