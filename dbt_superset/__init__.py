import typer
import logging
from .pull_dashboards import main as pull_dashboards_main
from .push_descriptions import main as push_descriptions_main

app = typer.Typer()

# logging.getLogger().setLevel(logging.DEBUG)

@app.command()
def pull_dashboards(dbt_project_dir: str = typer.Option('.', help="Directory path to dbt project."),
                    exposures_path: str = typer.Option('/models/exposures/superset_dashboards.yml',
                                                       help="Where within PROJECT_DIR the exposure file should "
                                                            "be stored. If you set this to go outside /models, it then "
                                                            "needs to be added to source-paths in dbt_project.yml."),
                    dbt_db_name: str = typer.Option(None, help="Name of your database within dbt towards which "
                                                               "the pull should be reduced to run."),
                    superset_url: str = typer.Argument(..., help="URL of your Superset, e.g. "
                                                                 "https://mysuperset.mycompany.com"),
                    superset_dashboard_url: str = typer.Option(None, help="Superset dashboard url to use in exposures"
                                                                   "Useful if the api and dashboard endpoints are different"),
                    superset_db_id: int = typer.Option(None, help="ID of your database within Superset towards which "
                                                                  "the pull should be reduced to run."),
                    sql_dialect: str = typer.Option('ansi', help="Database SQL dialect; used for parsing queries. "
                                                                 "Consult docs of SQLFluff for details: "
                                                                 "https://docs.sqlfluff.com/en/stable/dialects.html"),
                    username: str = typer.Option(None, envvar="USERNAME",
                                                              help="Username for Superset (check `superset fab` cli)"),
                    password: str = typer.Option(None, envvar="PASSWORD",
                                                               help="User password")):

    pull_dashboards_main(dbt_project_dir, exposures_path, dbt_db_name,
                         superset_url, superset_dashboard_url, superset_db_id, sql_dialect,
                         username, password)


@app.command()
def push_descriptions(dbt_project_dir: str = typer.Option('.', help="Directory path to dbt project."),
                      dbt_db_name: str = typer.Option(None, help="Name of your database within dbt to which the script "
                                                                 "should be reduced to run."),
                      superset_url: str = typer.Argument(..., help="Superset API URL, e.g. "
                                                                   "https://mysuperset.mycompany.com"),
                      superset_db_id: int = typer.Option(None, help="ID of your database within Superset towards which "
                                                                    "the push should be reduced to run."),
                      dataset_filter: str = typer.Option(None, help="Filter dataset to only include dataset with this name"
                                                                    "Useful to exclude non dbt models"),
                      superset_refresh_columns: bool = typer.Option(False, help="Whether columns in Superset should be "
                                                                                "refreshed from database before "
                                                                                "the push."),
                      superset_pause_after_update: int = typer.Option(2, help="Number of seconds for which the "
                                                                              "script pauses after any update of "
                                                                              "Superset columns. This is to allow "
                                                                              "databases to catch up in "
                                                                              "the meantime."),
                      default_descriptions_yaml_path: str = typer.Option(None,
                                                                         help="Yaml file with list of column and "
                                                                         "their descriptions which allows to set default "
                                                                         "descriptions for columns where None are provided in a model ."
                                                                         "This prevents duplication of effort ."
                                                                         "Format is 'columns': <column_name>: desc:'"),
                      username: str = typer.Option(None, envvar="USERNAME", help="Username for Superset (check `superset fab` cli)"),
                      password: str = typer.Option(None, envvar="PASSWORD", help="User password")):

    push_descriptions_main(dbt_project_dir, dbt_db_name, superset_url, superset_db_id,
                           dataset_filter, superset_refresh_columns, superset_pause_after_update,
                           default_descriptions_yaml_path, username, password)


if __name__ == '__main__':
    app()
