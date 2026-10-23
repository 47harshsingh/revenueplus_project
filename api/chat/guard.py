"""SQL guardrail for model-written queries.

check_sql() parses the SQL into a tree (sqlglot, Postgres dialect), rejects anything that
isn't exactly one read-only SELECT over the allowlisted KPI views, and returns SQL
*re-generated from the checked tree*, wrapped in LIMIT 500. What runs is exactly what was
inspected: comments and anything the parser dropped never reach the database.

This is one layer of several. The chatbot_ro role (db/04_roles.sql) would still refuse
anything that slipped through.
"""

import logging
import re

import sqlglot
from sqlglot import exp

logging.getLogger("sqlglot").setLevel(logging.ERROR)  # unparseable input is rejected below

ROW_LIMIT = 500
MAX_SQL_CHARS = 5000

ALLOWED_TABLES = frozenset({
    "v_as_of", "v_deal_facts", "v_monthly_region_kpis", "v_quota_attainment",
    "v_daily_region_metrics", "mv_funnel_monthly", "mv_pipeline_velocity", "mv_churn_risk",
})
# Real objects a CTE must not be named after: a CTE called fact_deals would otherwise
# make "FROM fact_deals" inside its own body look like a harmless CTE reference.
RESERVED_NAMES = ALLOWED_TABLES | {
    "fact_deals", "fact_activities", "dim_rep", "dim_account", "dim_product", "dim_date",
    "ground_truth", "anomalies",
}
# Functions are ALLOWLISTED: anything not named here (system, file, network, extension or
# user-defined functions like refresh_kpis) is rejected. A denylist would miss whatever we forgot.
ALLOWED_FUNCTIONS = frozenset({
    # aggregates
    "count", "sum", "avg", "min", "max", "string_agg", "array_agg", "bool_and", "bool_or",
    "every", "stddev", "stddev_pop", "stddev_samp", "variance", "var_pop", "var_samp", "corr",
    "covar_pop", "covar_samp", "regr_slope", "regr_intercept", "percentile_cont",
    "percentile_disc", "mode",
    # window
    "row_number", "rank", "dense_rank", "percent_rank", "cume_dist", "ntile", "lag", "lead",
    "first_value", "last_value", "nth_value",
    # conditional / casting (sqlglot renders CASE and ::type as function nodes)
    "case", "cast", "coalesce", "nullif", "greatest", "least",
    # math
    "round", "trunc", "floor", "ceil", "ceiling", "abs", "sign", "sqrt", "power", "exp", "ln",
    "log", "mod", "width_bucket",
    # dates
    "date_trunc", "date_part", "extract", "to_char", "to_date", "make_date", "age",
    "current_date", "generate_series", "date",
    # text
    "lower", "upper", "length", "concat", "substring", "substr", "trim", "replace",
    "split_part", "left", "right", "initcap",
    # set-returning (also allowed in FROM)
    "unnest",
})
ALLOWED_TABLE_FUNCTIONS = frozenset({"generate_series", "unnest"})
# Statement kinds that must not appear anywhere in the tree (including inside CTEs).
FORBIDDEN_NODES = tuple(
    getattr(exp, name) for name in (
        "Insert", "Update", "Delete", "Merge", "Create", "Drop", "Alter", "TruncateTable",
        "Copy", "Grant", "Revoke", "Command", "Set", "Transaction", "Commit", "Rollback",
        "Use", "Pragma", "Into", "Lock",
    ) if hasattr(exp, name)
)
QUERY_ROOTS = (exp.Select, exp.SetOperation)


class GuardRejected(Exception):
    """The SQL is not allowed. str(exc) is a short reason safe to show the user."""


def _function_name(node: exp.Func) -> str:
    """The name Postgres will actually see, taken from the re-generated SQL.

    sqlglot's internal labels differ (version() parses as CurrentVersion, generate_series
    as ExplodingGenerateSeries), so checking them would miss or wrongly block functions.
    Quoted ("pg_sleep") or schema-qualified (extensions.http_get) names fail closed.
    """
    rendered = node.sql(dialect="postgres", comments=False)
    match = re.match(r"\s*([A-Za-z_]\w*)(\s*\.)?", rendered)
    if not match or match.group(2):
        raise GuardRejected("quoted or schema-qualified function names are not allowed")
    return match.group(1).lower()


def check_sql(text: str) -> str:
    """Return safe, LIMIT-wrapped SQL, or raise GuardRejected."""
    if not text or not text.strip():
        raise GuardRejected("empty query")
    if len(text) > MAX_SQL_CHARS:
        raise GuardRejected("query too long")

    try:
        statements = [s for s in sqlglot.parse(text, read="postgres") if s is not None]
    except sqlglot.errors.SqlglotError:
        raise GuardRejected("could not parse the query") from None
    if len(statements) != 1:
        raise GuardRejected("exactly one statement is allowed")
    tree = statements[0]

    if not isinstance(tree, QUERY_ROOTS):
        raise GuardRejected("only SELECT queries are allowed")
    for node in tree.find_all(*FORBIDDEN_NODES):
        raise GuardRejected(f"{type(node).__name__.upper()} is not allowed")

    cte_names = set()
    for cte in tree.find_all(exp.CTE):
        name = cte.alias_or_name.lower()
        if name in RESERVED_NAMES or name.startswith("pg_"):
            raise GuardRejected(f"a CTE may not be named '{name}'")
        cte_names.add(name)

    for table in tree.find_all(exp.Table):
        if isinstance(table.this, exp.Func):  # function used as a table: FROM f(...)
            fname = _function_name(table.this)
            if fname not in ALLOWED_TABLE_FUNCTIONS:
                raise GuardRejected(f"function '{fname}' is not allowed in FROM")
            continue
        name, schema = table.name.lower(), table.db.lower()
        if table.catalog or (schema and schema != "public"):
            raise GuardRejected(f"schema '{table.catalog or schema}' is not allowed")
        if not schema and name in cte_names:
            continue
        if name not in ALLOWED_TABLES:
            raise GuardRejected(f"table '{name}' is not allowed")

    for func in tree.find_all(exp.Func):
        # sqlglot models operators (AND, OR, EXISTS, ~, JSON/array ops) as Func nodes too.
        # They aren't calls; their operands are still walked by find_all and checked here.
        if isinstance(func, (exp.Connector, exp.Binary, exp.Unary, exp.Predicate)):
            continue
        fname = _function_name(func)
        if fname not in ALLOWED_FUNCTIONS:
            raise GuardRejected(f"function '{fname}' is not allowed")

    inner = tree.sql(dialect="postgres", comments=False)
    return f"SELECT * FROM ({inner}) AS q LIMIT {ROW_LIMIT}"
