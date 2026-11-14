"""Инструмент визуализации графа зависимостей Maven-пакетов.

Этап 1. Минимальный прототип с конфигурацией: параметры читаются из
INI-файла, проверяются и выводятся на экран в формате ключ-значение.
"""

import argparse
import configparser
import re
import sys
from dataclasses import asdict, dataclass
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


class ConfigError(Exception):
    """Ошибка в конфигурационном файле или в значении параметра."""


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


def print_config(config):
    """Вывести все параметры пользователя в формате ключ-значение."""
    for key, value in asdict(config).items():
        print(f"{key} = {value}")


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


def main(argv=None):
    """Точка входа CLI-приложения."""
    args = parse_args(argv)
    try:
        config = load_config(args.config)
    except ConfigError as error:
        print(f"Ошибка конфигурации: {error}", file=sys.stderr)
        return EXIT_ERROR
    print_config(config)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
