#!/usr/bin/env python3
"""Подготовка Secret SQL-проб. Не обращается к Kubernetes и базам данных."""

import argparse
import base64
import json
import os
import sys
from urllib.parse import quote, urlencode


def build_config(environ):
    names = ("MSSQL_PROBE_USER", "MSSQL_PROBE_PASSWORD",
             "POSTGRES_PROBE_USER", "POSTGRES_PROBE_PASSWORD")
    if any(not environ.get(name) for name in names):
        raise ValueError("Заполни MSSQL_PROBE_USER/PASSWORD и POSTGRES_PROBE_USER/PASSWORD.")
    if any(any(ord(c) < 32 for c in environ[name]) for name in names):
        raise ValueError("Учётные данные не должны содержать управляющие символы.")

    def auth(prefix):
        return (quote(environ[prefix + "_USER"], safe="") + ":"
                + quote(environ[prefix + "_PASSWORD"], safe=""))

    # Параметры TLS совпадают с проверенными подключениями; сертификат MSSQL
    # самоподписанный, как в текущем приложении. CA/hostname здесь не проверяются.
    mssql = ("sqlserver://" + auth("MSSQL_PROBE") + "@192.168.0.2:1433?"
             + urlencode({"database": "joinrpg-prod", "encrypt": "true",
                          "TrustServerCertificate": "true", "connection timeout": "3"}))
    postgres = ("postgres://" + auth("POSTGRES_PROBE")
                + "@master.4b8029b8-1433-4452-b2e3-e171e7212ec6.c.dbaas.selcloud.ru:5433/"
                + "joinrpg_prod_notifications?sslmode=require&connect_timeout=3")
    return {
        "global": {
            "scrape_timeout": "5s", "scrape_timeout_offset": "500ms",
            "min_interval": "0s", "max_connections": 1, "max_idle_connections": 1,
            "max_connection_lifetime": "1s",
        },
        "jobs": [{
            "job_name": "sql-probes", "collectors": ["probe"],
            "static_configs": [{"targets": {"mssql": mssql, "postgres": postgres}}],
        }],
        "collectors": [{
            "collector_name": "probe",
            "metrics": [{"metric_name": "db_sql_probe_success", "type": "gauge",
                         "help": "SELECT 1 completed successfully", "values": ["success"],
                         "query": "SELECT 1 AS success"}],
        }],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--secret-stdout", action="store_true",
                        help="Вывести Secret с паролями: только в pipe kubectl apply -f -.")
    args = parser.parse_args()
    try:
        config = build_config(os.environ)
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    if args.secret_stdout:
        if sys.stdout.isatty():
            print("Secret не выводится в терминал. Используй pipe в kubectl apply -f -.",
                  file=sys.stderr)
            return 1
        encoded = base64.b64encode(json.dumps(config).encode()).decode()
        json.dump({"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
                   "metadata": {"name": "coroot-sql-probes", "namespace": "coroot"},
                   "data": {"sql_exporter.yml": encoded}}, sys.stdout)
        print()
    else:
        print("Конфигурация двух SQL-проб готова; Secret не выводился и не создавался.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
