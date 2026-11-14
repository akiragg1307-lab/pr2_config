"""Инструмент визуализации графа зависимостей Maven-пакетов.

Этап 2. Сбор данных: по URL Maven-репозитория скачивается POM-файл
пакета (с учётом родительских POM, свойств и dependencyManagement)
и выводятся все прямые зависимости. Сторонние библиотеки не нужны.
"""

import argparse
import configparser
import re
import sys
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

EXIT_OK = 0
EXIT_ERROR = 1
SECTION = "settings"
DEFAULT_CONFIG = "config.ini"
CONFIG_KEYS = ("package_name", "repository", "test_mode", "ascii_tree")
BOOLEAN_STATES = configparser.ConfigParser.BOOLEAN_STATES
URL_SCHEMES = ("http://", "https://")
TEST_NAME_RE = re.compile(r"^[A-Z]+$")
MAVEN_NAME_RE = re.compile(r"^[\w.\-]+:[\w.\-]+(:[\w.\-]+)?$")

HTTP_TIMEOUT = 15
USER_AGENT = "maven-deps-visualizer/1.0"
SHORT_COORDINATES = 2
DEFAULT_SCOPE = "compile"
SKIPPED_SCOPES = ("test", "provided", "system", "import")
UNKNOWN_VERSION = "unknown"
MAX_PARENT_DEPTH = 10
MAX_INTERPOLATION_PASSES = 5
PROPERTY_RE = re.compile(r"\$\{([^}]+)\}")
RAW_DEPENDENCY_KEYS = ("groupId", "artifactId", "version", "scope", "optional")


class ConfigError(Exception):
    """Ошибка в конфигурационном файле или в значении параметра."""


class RepositoryError(Exception):
    """Ошибка получения или разбора данных репозитория."""


@dataclass
class Config:
    """Параметры, настраиваемые пользователем."""

    package_name: str
    repository: str
    test_mode: bool
    ascii_tree: bool


def read_section(path):
    """Прочитать INI-файл и вернуть секцию [settings]."""
    file_path = Path(path)
    if not file_path.is_file():
        raise ConfigError(f"файл конфигурации не найден: {path}")
    parser = configparser.ConfigParser()
    try:
        parser.read(file_path, encoding="utf-8")
    except (configparser.Error, UnicodeDecodeError) as error:
        raise ConfigError(f"некорректный формат INI: {error}") from error
    if not parser.has_section(SECTION):
        raise ConfigError(f"отсутствует секция [{SECTION}]")
    return parser[SECTION]


def check_unknown_keys(section):
    """Убедиться, что в секции нет неизвестных параметров."""
    unknown = sorted(set(section) - set(CONFIG_KEYS))
    if unknown:
        raise ConfigError("неизвестные параметры: " + ", ".join(unknown))


def get_value(section, key):
    """Получить обязательный непустой строковый параметр."""
    if key not in section:
        raise ConfigError(f"отсутствует параметр '{key}'")
    value = section[key].strip()
    if not value:
        raise ConfigError(f"параметр '{key}' не может быть пустым")
    return value


def get_flag(section, key):
    """Получить обязательный логический параметр (true/false)."""
    value = get_value(section, key)
    if value.lower() not in BOOLEAN_STATES:
        raise ConfigError(
            f"параметр '{key}' должен быть true или false, "
            f"получено '{value}'"
        )
    return BOOLEAN_STATES[value.lower()]


def validate_package(name, test_mode):
    """Проверить формат имени пакета для выбранного режима."""
    if test_mode and not TEST_NAME_RE.match(name):
        raise ConfigError(
            f"некорректное имя пакета '{name}': в тестовом режиме "
            "ожидаются большие латинские буквы (например, A)"
        )
    if not test_mode and not MAVEN_NAME_RE.match(name):
        raise ConfigError(
            f"некорректное имя пакета '{name}': ожидается "
            "groupId:artifactId или groupId:artifactId:version"
        )


def validate_repository(repository, test_mode):
    """Проверить URL репозитория или путь к тестовому файлу."""
    if test_mode and not Path(repository).is_file():
        raise ConfigError(
            f"файл тестового репозитория не найден: {repository}"
        )
    if not test_mode and not repository.startswith(URL_SCHEMES):
        raise ConfigError(
            "URL репозитория должен начинаться с http:// или "
            f"https://, получено '{repository}'"
        )


def load_config(path):
    """Загрузить и проверить все параметры из INI-файла."""
    section = read_section(path)
    check_unknown_keys(section)
    config = Config(
        package_name=get_value(section, "package_name"),
        repository=get_value(section, "repository"),
        test_mode=get_flag(section, "test_mode"),
        ascii_tree=get_flag(section, "ascii_tree"),
    )
    validate_package(config.package_name, config.test_mode)
    validate_repository(config.repository, config.test_mode)
    return config


@dataclass
class Dependency:
    """Зависимость из POM-файла после подстановки свойств."""

    group_id: str
    artifact_id: str
    version: str
    scope: str = DEFAULT_SCOPE
    optional: bool = False

    @property
    def name(self):
        """Вернуть координаты в виде groupId:artifactId:version."""
        return f"{self.group_id}:{self.artifact_id}:{self.version}"

    def is_transitive(self):
        """Проверить, наследуется ли зависимость транзитивно."""
        return self.scope not in SKIPPED_SCOPES and not self.optional


@dataclass
class Pom:
    """Данные одного POM-файла без учёта наследования."""

    group_id: str
    artifact_id: str
    version: str
    parent: tuple = None
    properties: dict = field(default_factory=dict)
    management: dict = field(default_factory=dict)
    dependencies: list = field(default_factory=list)


def fetch(url):
    """Скачать содержимое по URL средствами стандартной библиотеки."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as answer:
            return answer.read()
    except urllib.error.HTTPError as error:
        raise RepositoryError(f"HTTP {error.code}: {url}") from error
    except (OSError, ValueError) as error:
        raise RepositoryError(f"не удалось загрузить {url}: {error}") from error


def parse_xml(data):
    """Разобрать XML и убрать пространства имён из тегов."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise RepositoryError(f"некорректный XML: {error}") from error
    for element in root.iter():
        if isinstance(element.tag, str):
            element.tag = element.tag.rsplit("}", 1)[-1]
    return root


def text_of(element, path):
    """Вернуть очищенный текст дочернего элемента или пустую строку."""
    return (element.findtext(path) or "").strip()


def parse_raw_dependency(element):
    """Прочитать элемент <dependency> как словарь строк."""
    return {key: text_of(element, key) for key in RAW_DEPENDENCY_KEYS}


def parse_parent(root):
    """Вернуть координаты родительского POM или None."""
    parent = root.find("parent")
    if parent is None:
        return None
    return tuple(
        text_of(parent, key) for key in ("groupId", "artifactId", "version")
    )


def parse_pom(data):
    """Разобрать POM-файл в объект Pom."""
    root = parse_xml(data)
    parent = parse_parent(root)
    group_id = text_of(root, "groupId") or (parent[0] if parent else "")
    version = text_of(root, "version") or (parent[2] if parent else "")
    managed = root.findall("dependencyManagement/dependencies/dependency")
    management = {}
    for element in managed:
        raw = parse_raw_dependency(element)
        management[f"{raw['groupId']}:{raw['artifactId']}"] = raw
    return Pom(
        group_id=group_id,
        artifact_id=text_of(root, "artifactId"),
        version=version,
        parent=parent,
        properties={
            item.tag: (item.text or "").strip()
            for item in root.findall("properties/*")
        },
        management=management,
        dependencies=[
            parse_raw_dependency(item)
            for item in root.findall("dependencies/dependency")
        ],
    )


def interpolate(value, properties):
    """Подставить значения свойств вида ${name} в строку."""
    for _ in range(MAX_INTERPOLATION_PASSES):
        result = PROPERTY_RE.sub(
            lambda match: properties.get(match.group(1), match.group(0)),
            value,
        )
        if result == value:
            break
        value = result
    return value


def pick_from_range(version):
    """Выбрать конкретную версию из диапазона Maven, например [1.0,2.0)."""
    if not version.startswith(("[", "(")):
        return version
    bounds = [item.strip() for item in version.strip("[]()").split(",")]
    candidates = [item for item in bounds if item]
    return candidates[0] if candidates else ""


def project_properties(pom):
    """Вернуть встроенные свойства project.* для подстановки."""
    properties = {
        "project.groupId": pom.group_id,
        "project.artifactId": pom.artifact_id,
        "project.version": pom.version,
        "pom.version": pom.version,
        "version": pom.version,
    }
    if pom.parent:
        properties["project.parent.groupId"] = pom.parent[0]
        properties["project.parent.version"] = pom.parent[2]
    return properties


class MavenRepository:
    """Клиент Maven-репозитория, работающий напрямую с POM-файлами."""

    def __init__(self, base_url):
        """Запомнить базовый URL репозитория и создать кэш POM."""
        self.base_url = base_url.rstrip("/")
        self._poms = {}

    def _url(self, group_id, artifact_id, *parts):
        """Построить URL файла внутри репозитория."""
        path = "/".join([group_id.replace(".", "/"), artifact_id, *parts])
        return f"{self.base_url}/{path}"

    def latest_version(self, group_id, artifact_id):
        """Определить последнюю версию по maven-metadata.xml."""
        url = self._url(group_id, artifact_id, "maven-metadata.xml")
        root = parse_xml(fetch(url))
        version = text_of(root, "versioning/release")
        version = version or text_of(root, "versioning/latest")
        versions = root.findall("versioning/versions/version")
        if not version and versions:
            version = (versions[-1].text or "").strip()
        if not version:
            raise RepositoryError(f"нет версий в {url}")
        return version

    def load_pom(self, group_id, artifact_id, version):
        """Скачать и разобрать POM-файл (с кэшированием)."""
        key = (group_id, artifact_id, version)
        if key not in self._poms:
            file_name = f"{artifact_id}-{version}.pom"
            url = self._url(group_id, artifact_id, version, file_name)
            self._poms[key] = parse_pom(fetch(url))
        return self._poms[key]

    def canonical_name(self, name):
        """Дополнить имя пакета версией, если она не указана."""
        parts = name.split(":")
        if len(parts) == SHORT_COORDINATES:
            parts.append(self.latest_version(*parts))
        return ":".join(parts)

    def build_context(self, pom):
        """Собрать свойства и dependencyManagement с учётом родителей."""
        chain = [pom]
        while chain[-1].parent and len(chain) < MAX_PARENT_DEPTH:
            chain.append(self.load_pom(*chain[-1].parent))
        properties, management = {}, {}
        for item in reversed(chain):
            properties.update(item.properties)
            management.update(item.management)
        properties.update(project_properties(pom))
        management = {
            interpolate(key, properties): value
            for key, value in management.items()
        }
        return properties, management

    def _version_or_latest(self, group_id, artifact_id, version):
        """Вернуть версию; если её нельзя вычислить — взять последнюю."""
        if version and "${" not in version:
            return version
        try:
            return self.latest_version(group_id, artifact_id)
        except RepositoryError:
            return UNKNOWN_VERSION

    def resolve(self, raw, properties, management):
        """Превратить описание <dependency> в объект Dependency."""
        group_id = interpolate(raw["groupId"], properties)
        artifact_id = interpolate(raw["artifactId"], properties)
        managed = management.get(f"{group_id}:{artifact_id}", {})
        version = raw["version"] or managed.get("version", "")
        version = pick_from_range(interpolate(version, properties))
        scope = raw["scope"] or managed.get("scope", "") or DEFAULT_SCOPE
        return Dependency(
            group_id=group_id,
            artifact_id=artifact_id,
            version=self._version_or_latest(group_id, artifact_id, version),
            scope=interpolate(scope, properties),
            optional=interpolate(raw["optional"], properties) == "true",
        )

    def direct_dependencies(self, name):
        """Вернуть все прямые зависимости пакета groupId:artifactId:ver."""
        group_id, artifact_id, version = name.split(":")
        pom = self.load_pom(group_id, artifact_id, version)
        properties, management = self.build_context(pom)
        return [
            self.resolve(raw, properties, management)
            for raw in pom.dependencies
        ]


def print_direct_dependencies(name, dependencies):
    """Вывести все прямые зависимости пакета с их областью (scope)."""
    print(f"Прямые зависимости пакета {name}:")
    for item in dependencies:
        flags = item.scope + (", optional" if item.optional else "")
        print(f"  {item.name} [{flags}]")
    if not dependencies:
        print("  (нет зависимостей)")
    print(f"Всего: {len(dependencies)}")


def parse_args(argv):
    """Разобрать аргументы командной строки."""
    parser = argparse.ArgumentParser(
        description="Визуализатор графа зависимостей Maven-пакетов"
    )
    parser.add_argument(
        "-c", "--config", default=DEFAULT_CONFIG,
        help="путь к конфигурационному INI-файлу",
    )
    return parser.parse_args(argv)


def run(args):
    """Выполнить программу и вернуть код завершения."""
    try:
        config = load_config(args.config)
        if config.test_mode:
            raise ConfigError("тестовый режим поддерживается с этапа 3")
        repository = MavenRepository(config.repository)
        name = repository.canonical_name(config.package_name)
        print_direct_dependencies(name, repository.direct_dependencies(name))
    except ConfigError as error:
        print(f"Ошибка конфигурации: {error}", file=sys.stderr)
        return EXIT_ERROR
    except RepositoryError as error:
        print(f"Ошибка репозитория: {error}", file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


def main(argv=None):
    """Точка входа CLI-приложения."""
    return run(parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
