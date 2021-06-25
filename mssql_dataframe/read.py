from typing import Literal

import pandas as pd

from mssql_dataframe import errors
from mssql_dataframe import helpers


def select(connection, table_name: str, column_names: list = None, where: str = None,
limit: int = None, order_column: str=None, order_direction: Literal[None,'ASC','DESC'] = None) -> pd.DataFrame:
    """Select data from SQL into a dataframe.

    Parameters
    ----------

    connection (mssql_dataframe.connect) : connection for executing statement
    table_name (str) : name of table to select data frame
    column_names (list, default=None) : list of columns to select, or None to select all
    where (list, default=None) : where clause filter to apply
    limit (int, default=None) : select limited number of records only
    order_column (str, default=None) : order results by column
    order_direction (str, default=None) : order direction

    Returns
    -------

    dataframe (pd.DataFrame): tabular data from select statement
    
    None

    Examples
    --------



    """

    schema = helpers.get_schema(connection, table_name)
    primary_key = list(schema[schema['is_primary_key']].index)

    # sanitize table and column names for safe sql
    table_clean = helpers.safe_sql(connection, table_name)
    if column_names is None:
        column_names = '*'
    else:
        column_names = [x for x in primary_key if x not in column_names]+column_names
        column_names = helpers.safe_sql(connection, column_names)
        column_names = "\n,".join(column_names)

    # format optional where_statement
    if where is None:
        where_statement, where_args = ("", None)
    else:
        where_statement, where_args = helpers.where_clause(connection, where)

    # format optional limit
    if limit is None:
        limit = ""
    elif not isinstance(limit,int):
        raise ValueError("limit must be an integer")
    else:
        limit = "TOP("+str(limit)+")"

    # format optional order
    options = [None,'ASC','DESC']
    if (order_column is None and order_direction is not None) or (order_column is not None and order_direction is None):
        raise ValueError("order_column and order_direction must both be specified")
    elif order_direction not in options:
        raise ValueError("order direction must be one of: "+str(options))
    elif order_column is not None:
        order = "ORDER BY "+helpers.safe_sql(connection, order_column)+" "+order_direction
    else:
        order = ""


    # select values
    statement = """
    SELECT {limit}
        {column_names}
    FROM
        {table_name}
        {where_statement}
        {order}
    """.format(limit=limit,
        column_names=column_names, 
        table_name=table_clean, 
        where_statement=where_statement, 
        order=order
    )

    # read sql query
    try:
        if where_args is None:
            dataframe = helpers.read_query(connection, statement)
        else:
            dataframe = helpers.read_query(connection, statement, where_args)
    except:
        raise errors.GeneralError("GeneralError") from None

    # change to best datatype
    dtypes = schema['python_type'].reset_index().values
    dtypes = {x[0]:x[1] for x in dtypes if x[0] in dataframe.columns}
    dataframe = dataframe.astype(dtypes)

    # set dataframe index as primary key
    if len(primary_key)>0:
        dataframe = dataframe.set_index(keys=primary_key)
        # use lowercase version, which represents non-nullable datatype for example, int64 for Int64
        dataframe.index = dataframe.index.astype(dtypes[primary_key[0]].lower())

    return dataframe