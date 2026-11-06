"""Проверки конфигурации SQL-проб без доступа к реальным БД и секретам."""

import importlib.util
from pathlib import Path
import unittest
from urllib.parse import parse_qs, unquote, urlsplit


spec = importlib.util.spec_from_file_location(
    "sql_probe", Path(__file__).with_name("build-sql-probe-secret.py"))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ProbeConfigTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            "MSSQL_PROBE_USER": "probe@ms", "MSSQL_PROBE_PASSWORD": "a'\"@:/?%+$()",
            "POSTGRES_PROBE_USER": "probe@pg", "POSTGRES_PROBE_PASSWORD": "b'\"@:/?%+$()",
        }

    def test_credentials_survive_url_encoding(self):
        targets = module.build_config(self.env)["jobs"][0]["static_configs"][0]["targets"]
        for target, prefix in (("mssql", "MSSQL_PROBE"), ("postgres", "POSTGRES_PROBE")):
            url = urlsplit(targets[target])
            self.assertEqual(unquote(url.username), self.env[prefix + "_USER"])
            self.assertEqual(unquote(url.password), self.env[prefix + "_PASSWORD"])
        self.assertEqual(parse_qs(urlsplit(targets["mssql"]).query)["database"], ["joinrpg-prod"])
        self.assertEqual(urlsplit(targets["postgres"]).port, 5433)

    def test_only_readonly_query_and_short_connection_lifetime(self):
        config = module.build_config(self.env)
        self.assertEqual(config["collectors"][0]["metrics"][0]["query"], "SELECT 1 AS success")
        self.assertEqual(config["global"]["min_interval"], "0s")
        self.assertEqual(config["global"]["max_connection_lifetime"], "1s")
        self.assertEqual(config["global"]["max_connections"], 1)

    def test_missing_credentials_do_not_expose_values(self):
        self.env["POSTGRES_PROBE_PASSWORD"] = ""
        with self.assertRaises(ValueError) as error:
            module.build_config(self.env)
        self.assertNotIn(self.env["MSSQL_PROBE_PASSWORD"], str(error.exception))

    def test_control_characters_rejected(self):
        self.env["MSSQL_PROBE_PASSWORD"] = "password\nother"
        with self.assertRaises(ValueError):
            module.build_config(self.env)


if __name__ == "__main__":
    unittest.main()
