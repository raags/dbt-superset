import json
import logging
import re
import time
import ruamel.yaml
import pathlib

from bs4 import BeautifulSoup
from markdown import markdown
from requests import HTTPError

from .superset_api import Superset

logging.basicConfig(format='%(asctime)s - %(levelname)s - %(message)s', level=logging.INFO)


def get_datasets_from_superset(superset, superset_db_id, dataset_filter=None):
    logging.info("Getting physical datasets from Superset.")

    page_number = 0
    datasets = []
    datasets_keys = set()
    while True:
        logging.info("Getting page %d.", page_number + 1)

        payload = {
            'q': json.dumps({
                'page': page_number,
                'page_size': 100
            })
        }
        res = superset.request('GET', '/dataset/', params=payload)

        result = res['result']
        if result:
            for r in result:
                kind = r['kind']
                database_id = r['database']['id']

                if kind == 'physical' \
                        and (superset_db_id is None or database_id == superset_db_id):

                    dataset_id = r['id']

                    name = r['table_name']
                    schema = r['schema']
                    dataset_key = f'{schema}.{name}'  # used as unique identifier

                    if dataset_filter and dataset_filter not in dataset_key:
                        logging.info(f"Skipping {dataset_key} for db id {dataset_id} due to schema filter: `{dataset_filter}`")
                        continue
                    else:
                        logging.info(f"Matched {dataset_key} for db id {dataset_id} due to schema filter: `{dataset_filter}`")

                    dataset_dict = {
                        'id': dataset_id,
                        'key': dataset_key
                    }

                    # fail if it breaks uniqueness constraint
                    assert dataset_key not in datasets_keys, \
                        f"Dataset {dataset_key} is a duplicate name (schema + table) " \
                        "across databases. " \
                        "This would result in incorrect matching between Superset and dbt. " \
                        "To fix this, remove duplicates or add the ``superset_db_id`` argument."

                    datasets_keys.add(dataset_key)
                    datasets.append(dataset_dict)
            page_number += 1
        else:
            break

    assert datasets, "There are no datasets in Superset!"

    return datasets

def update_dbt_with_default_desc(dbt_manifest, dbt_catalog, default_descriptions):
    default_column_descs = default_descriptions.get('columns', {})

    for table_type in ['nodes', 'sources']:
        manifest_subset = dbt_manifest[table_type]
        catalog_subset = dbt_catalog[table_type]

        # catalog lists all tables and columns, manifest only contains a subset
        for table_key_long in catalog_subset:
            catalog_table = catalog_subset[table_key_long]
            catalog_table_name = catalog_table['metadata']['name']
            catalog_table_columns = catalog_table['columns']

            manifest_table = manifest_subset[table_key_long]
            manifest_table_columns = manifest_table['columns']

            for catalog_column in catalog_table_columns:
                logging.debug(f'Processing for dbt catalog table {catalog_table_name} - {catalog_column}')
                if catalog_column not in manifest_table_columns and catalog_column in default_column_descs:
                    # add column with the default description
                    logging.info(f'Adding default desc for {catalog_table_name} - {catalog_column}')
                    manifest_subset[table_key_long]['columns'][catalog_column] = {
                        "name": catalog_column,
                        "description": default_column_descs[catalog_column]['desc'],
                        "meta": {},
                        "data_type": None,
                        "quote": None,
                        "tags": []
                    }

        dbt_manifest[table_type] = manifest_subset

    return dbt_manifest

def get_tables_from_dbt(dbt_manifest, dbt_db_name):
    tables = {}
    for table_type in ['nodes', 'sources']:
        manifest_subset = dbt_manifest[table_type]

        for table_key_long in manifest_subset:
            table = manifest_subset[table_key_long]
            name = table['name']
            schema = table['schema']
            database = table['database']

            table_key_short = schema + '.' + name
            columns = table['columns']
            description = table['description']

            if dbt_db_name is None or database == dbt_db_name:
                # fail if it breaks uniqueness constraint
                assert table_key_short not in tables, \
                    f"Table {table_key_short} is a duplicate name (schema + table) " \
                    f"across databases. " \
                    "This would result in incorrect matching between Superset and dbt. " \
                    "To fix this, remove duplicates or add the ``dbt_db_name`` argument."

                tables[table_key_short] = {'columns': columns, 'description': description}

    assert tables, "Manifest is empty!"

    return tables

def get_default_column_desc(default_descriptions_yaml_path):
    if default_descriptions_yaml_path:
        yaml = ruamel.yaml.YAML(typ='safe')
        return yaml.load(pathlib.Path(default_descriptions_yaml_path))
    else:
        return {}

def refresh_columns_in_superset(superset, dataset_id):
    logging.info("Refreshing columns in Superset.")
    superset.request('PUT', f'/dataset/{dataset_id}/refresh')


def add_superset_columns(superset, dataset):
    logging.info("Pulling fresh columns info from Superset.")

    res = superset.request('GET', f"/dataset/{dataset['id']}")
    result = res['result']

    dataset['columns'] = result['columns']
    dataset['description'] = result['description']
    dataset['owners'] = result['owners']

    return dataset


def convert_markdown_to_plain_text(md_string):
    """Converts a markdown string to plaintext.

    The following solution is used:
    https://gist.github.com/lorey/eb15a7f3338f959a78cc3661fbc255fe
    """

    # md -> html -> text since BeautifulSoup can extract text cleanly
    html = markdown(md_string)

    # remove code snippets
    html = re.sub(r'<pre>(.*?)</pre>', ' ', html)
    html = re.sub(r'<code>(.*?)</code >', ' ', html)

    # extract text
    soup = BeautifulSoup(html, 'html.parser')
    text = ''.join(soup.findAll(text=True))

    # make one line
    single_line = re.sub(r'\s+', ' ', text)

    # make fixes
    single_line = re.sub('→', '->', single_line)
    single_line = re.sub('<null>', '"null"', single_line)

    return single_line


def merge_columns_info(dataset, tables, default_descriptions):

    key = dataset['key']
    sst_columns = dataset['columns']
    dbt_columns = tables.get(key, {}).get('columns', {})
    default_column_descs = default_descriptions.get('columns', {})

    sst_description = dataset['description']
    dbt_description = tables.get(key, {}).get('description')

    sst_owners = dataset['owners']

    columns_new = []
    for sst_column in sst_columns:

        column_name = sst_column['column_name']

        # add the mandatory fields
        column_new = {
            'column_name': column_name,
            'id': sst_column['id']
        }

        # add column descriptions
        description = sst_column['description']
        # ensure database column
        if sst_column['expression'] is None or sst_column['expression'] == '':
            if column_name in dbt_columns and 'description' in dbt_columns[column_name]:
                description = convert_markdown_to_plain_text(
                    dbt_columns[column_name]['description']
                )
            # fallback to default descriptions defined in yaml file
            elif column_name in default_column_descs and 'desc' in default_column_descs[column_name]:
                description = convert_markdown_to_plain_text(
                    default_column_descs[column_name]['desc']
                )

        column_new['description'] = description

        columns_new.append(column_new)

    dataset['columns_new'] = columns_new

    # add dataset description
    if dbt_description is None:
        dataset['description_new'] = sst_description
    else:
        dataset['description_new'] = convert_markdown_to_plain_text(dbt_description)

    # add dataset owner IDs (otherwise Superset empties the owners list)
    dataset['owners_new'] = [owner['id'] for owner in sst_owners]

    return dataset


def check_columns_equal(lst1, lst2):
    return sorted(lst1, key=lambda c: c["id"]) == sorted(lst2, key=lambda c: c["id"])


def pause_after_update(superset_pause_after_update):
    if superset_pause_after_update:
        logging.info("Pausing the script for %d seconds to allow for databases to catch up with the update.",
                     superset_pause_after_update)
        time.sleep(superset_pause_after_update)
        logging.info("Resuming the script again.")


def put_descriptions_to_superset(superset, dataset, superset_pause_after_update):
    logging.info("Putting model and column descriptions into Superset.")

    description_new = dataset['description_new']
    columns_new = dataset['columns_new']
    owners_new = dataset['owners_new']

    description_old = dataset['description']
    columns_old = [{
        'column_name': col['column_name'],
        'id': col['id'],
        'description': col['description']
    } for col in dataset['columns']]

    if description_new != description_old or \
       not check_columns_equal(columns_new, columns_old):
        payload = {'description': description_new, 'columns': columns_new, 'owners': owners_new}
        logging.info(f"Adding new descriptions for dataset {dataset['key']}")
        superset.request('PUT', f"/dataset/{dataset['id']}?override_columns=false", json=payload)
        pause_after_update(superset_pause_after_update)
    else:
        logging.info("Skipping PUT execute request as nothing would be updated.")


def main(dbt_project_dir, dbt_db_name, superset_url, superset_db_id,
         dataset_filter, superset_refresh_columns, superset_pause_after_update,
         default_descriptions_yaml_path, username, password):

    # require creds
    assert username is not None or superset_refresh_token is not None, \
           "Add ``USERNAME`` or ``PASSWORD`` " \
           "to your environment variables or provide in CLI " \
           "via ``username`` or ``password``."

    logging.info("Starting the script!")

    superset = Superset(superset_url + '/api/v1',
                        username=username, password=password)

    sst_datasets = get_datasets_from_superset(superset, superset_db_id, dataset_filter)
    logging.info("There are %d physical datasets in Superset overall.", len(sst_datasets))

    with open(f'{dbt_project_dir}/target/manifest.json') as f:
        dbt_manifest = json.load(f)

    # update dbt docs with default descriptions in manifest first
    # TODO: potentially no need to apply default descs again when pushing to superset
    #       see put_descriptions_to_superset
    default_descriptions = get_default_column_desc(default_descriptions_yaml_path)
    if default_descriptions:
        with open(f'{dbt_project_dir}/target/catalog.json') as f:
            dbt_catalog = json.load(f)
        updated_dbt_manifest = update_dbt_with_default_desc(dbt_manifest, dbt_catalog, default_descriptions)
        logging.info("Updating Dbt manifest with default descriptions")
        with open(f'{dbt_project_dir}/target/manifest.json', 'w') as f:
            json.dump(updated_dbt_manifest, f, indent=4)
        dbt_manifest = updated_dbt_manifest

    # push desc to superset
    dbt_tables = get_tables_from_dbt(dbt_manifest, dbt_db_name)

    sst_datasets_dbt_filtered = [d for d in sst_datasets if d["key"] in dbt_tables]
    logging.info("There are %d physical datasets in Superset with a match in dbt.", len(sst_datasets_dbt_filtered))

    for i, sst_dataset in enumerate(sst_datasets_dbt_filtered):
        logging.info("Processing dataset %d/%d.", i + 1, len(sst_datasets_dbt_filtered))
        sst_dataset_id = sst_dataset['id']
        try:
            if superset_refresh_columns:
                refresh_columns_in_superset(superset, sst_dataset_id)
                pause_after_update(superset_pause_after_update)
            sst_dataset_w_cols = add_superset_columns(superset, sst_dataset)
            sst_dataset_w_cols_new = merge_columns_info(sst_dataset_w_cols, dbt_tables, default_descriptions)
            put_descriptions_to_superset(superset, sst_dataset_w_cols_new, superset_pause_after_update)
        except HTTPError as e:
            logging.error("The dataset with ID=%d wasn't updated. Check the error below.",
                          sst_dataset_id, exc_info=e)

    logging.info("All done!")
