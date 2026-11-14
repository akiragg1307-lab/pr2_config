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

POM_XML = b"""<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
  <parent>
    <groupId>org.example</groupId>
    <artifactId>parent</artifactId>
    <version>7</version>
  </parent>
  <artifactId>app</artifactId>
  <properties><lib.version>2.1</lib.version></properties>
  <dependencies>
    <dependency>
      <groupId>org.example</groupId>
      <artifactId>lib</artifactId>
      <version>${lib.version}</version>
    </dependency>
    <dependency>
      <groupId>junit</groupId>
      <artifactId>junit</artifactId>
      <version>4.13.2</version>
      <scope>test</scope>
    </dependency>
  </dependencies>
</project>
"""


class TestPom(unittest.TestCase):
    """Этап 2: разбор POM-файлов без сторонних библиотек."""

    def test_parse_pom_inherits_parent(self):
        """groupId и version наследуются от родителя."""
        pom = main.parse_pom(POM_XML)
        self.assertEqual(pom.group_id, "org.example")
        self.assertEqual(pom.version, "7")
        self.assertEqual(pom.parent, ("org.example", "parent", "7"))
        self.assertEqual(len(pom.dependencies), len(["lib", "junit"]))

    def test_interpolate(self):
        """Свойства ${...} подставляются, в том числе вложенные."""
        props = {"a": "${b}", "b": "1.0"}
        self.assertEqual(main.interpolate("v${a}", props), "v1.0")
        self.assertEqual(main.interpolate("${x}", props), "${x}")

    def test_pick_from_range(self):
        """Из диапазона версий выбирается граница."""
        self.assertEqual(main.pick_from_range("[1.2,2.0)"), "1.2")
        self.assertEqual(main.pick_from_range("(,3.0]"), "3.0")
        self.assertEqual(main.pick_from_range("4.0"), "4.0")

    def test_resolve_uses_management(self):
        """Версия без <version> берётся из dependencyManagement."""
        repository = main.MavenRepository("https://example.org/maven2")
        raw = dict.fromkeys(main.RAW_DEPENDENCY_KEYS, "")
        raw.update(groupId="org.example", artifactId="core")
        management = {"org.example:core": {"version": "${core.v}"}}
        dependency = repository.resolve(raw, {"core.v": "3.3"}, management)
        self.assertEqual(dependency.name, "org.example:core:3.3")
        self.assertTrue(dependency.is_transitive())

    def test_test_scope_not_transitive(self):
        """Зависимости test и optional не попадают в граф."""
        test_dep = main.Dependency("g", "a", "1", scope="test")
        optional_dep = main.Dependency("g", "b", "1", optional=True)
        self.assertFalse(test_dep.is_transitive())
        self.assertFalse(optional_dep.is_transitive())

class FakeSource:
    """Источник зависимостей на основе словаря (для тестов)."""

    def __init__(self, graph):
        """Запомнить граф."""
        self.graph = graph

    def canonical_name(self, name):
        """Вернуть имя без изменений."""
        return name

    def dependencies(self, name):
        """Вернуть зависимости узла."""
        return list(self.graph.get(name, []))


class TestGraph(TempDirTestCase):
    """Этап 3: построение графа DFS без рекурсии, циклы."""

    def test_parse_test_repository(self):
        """Файл тестового репозитория разбирается в словарь."""
        graph = main.parse_test_repository("# comment\nA: B, C\nB: C\nC:\n")
        self.assertEqual(graph, {"A": ["B", "C"], "B": ["C"], "C": []})

    def test_parse_rejects_bad_names(self):
        """Имена не из больших латинских букв — ошибка."""
        with self.assertRaises(main.RepositoryError):
            main.parse_test_repository("A: b\n")

    def test_local_repository_missing_package(self):
        """Корневой пакет должен быть описан в файле."""
        repo = main.LocalTestRepository(self.write("r.txt", "A: B\n"))
        with self.assertRaises(main.RepositoryError):
            repo.canonical_name("Z")

    def test_transitive_dependencies(self):
        """Граф содержит транзитивные зависимости, каждую один раз."""
        source = FakeSource({"A": ["B", "C"], "B": ["D"], "C": ["D"]})
        graph = main.build_graph(source, "A")
        self.assertEqual(set(graph.edges), {"A", "B", "C", "D"})
        self.assertEqual(graph.cycles, [])

    def test_cycle_detected(self):
        """Цикл находится, обход завершается."""
        source = FakeSource({"A": ["B"], "B": ["C"], "C": ["A"]})
        graph = main.build_graph(source, "A")
        self.assertEqual(graph.cycles, [["A", "B", "C", "A"]])

    def test_self_loop(self):
        """Петля пакета на самого себя тоже является циклом."""
        graph = main.build_graph(FakeSource({"A": ["A"]}), "A")
        self.assertEqual(graph.cycles, [["A", "A"]])

    def test_deep_chain_without_recursion(self):
        """Длинная цепочка не вызывает RecursionError."""
        length = sys.getrecursionlimit() * len("AB")
        chain = {f"N{i}": [f"N{i + 1}"] for i in range(length)}
        graph = main.build_graph(FakeSource(chain), "N0")
        self.assertEqual(len(graph.edges), length + 1)

class TestLoadOrder(unittest.TestCase):
    """Этап 4: порядок загрузки зависимостей."""

    def order_for(self, edges):
        """Построить граф и вернуть порядок загрузки."""
        return main.load_order(main.build_graph(FakeSource(edges), "A"))

    def test_dependencies_before_dependents(self):
        """Каждая зависимость загружается раньше зависящего пакета."""
        edges = {"A": ["B", "C"], "B": ["D"], "C": ["D"], "D": ["E"]}
        order = self.order_for(edges)
        self.assertEqual(order[-1], "A")
        for name, children in edges.items():
            for child in children:
                self.assertLess(order.index(child), order.index(name))

    def test_each_package_once(self):
        """Общая зависимость присутствует в порядке один раз."""
        order = self.order_for({"A": ["B", "C"], "B": ["D"], "C": ["D"]})
        self.assertEqual(sorted(order), ["A", "B", "C", "D"])

    def test_cycle_does_not_hang(self):
        """При цикле порядок строится, каждый узел один раз."""
        order = self.order_for({"A": ["B"], "B": ["C"], "C": ["A"]})
        self.assertEqual(order, ["C", "B", "A"])
