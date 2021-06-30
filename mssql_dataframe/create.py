from typing import Literal

import pandas as pd
import numpy as np

from mssql_dataframe import helpers
from mssql_dataframe import write

def table(connection, table_name: str, columns: dict, not_null: list = [],
primary_key_column: str = None, sql_primary_key: bool = False):
    """Create SQL table by explicitly specifying SQL create table parameters.

    Parameters
    ----------

    connection (mssql_dataframe.connect) : connection for executing statement
    table_name (str) : name of table to create
    columns (dict) : keys = column names, values = data types and optionally size/precision
    not_null (list, default=[]) : list of columns to set as not null
    primary_key_column (str, default=None) : column to set as the primary key
    sql_primary_key (bool, default=False) : create an INT SQL identity column as the primary key named _pk

    Returns
    -------

    None

    Example
    -------
    
    create_table(table_name='##SingleColumnTable', columns={"A": "VARCHAR(100)"})

    """
    
    statement = """
    DECLARE @SQLStatement AS NVARCHAR(MAX);
    DECLARE @TableName SYSNAME = ?;
    {declare}
    SET @SQLStatement = N'CREATE TABLE '+QUOTENAME(@TableName)+' ('+
    {syntax}
    +');'
    EXEC sp_executesql
    @SQLStatement,
    N'@TableName SYSNAME, {parameters}',
    @TableName=@TableName, {values};
    """

    column_names = list(columns.keys())
    alias_names = [str(x) for x in list(range(0,len(column_names)))]
    size, dtypes = helpers.column_spec(columns.values())
    size_vars = [alias_names[idx] if x is not None else None for idx,x in enumerate(size)]

    # develop syntax for SQL variable declaration
    declare = list(zip(
        ["DECLARE @ColumnName_"+x+" SYSNAME = ?;" for x in alias_names],
        ["DECLARE @ColumnType_"+x+" SYSNAME = ?;" for x in alias_names],
        ["DECLARE @ColumnSize_"+x+" SYSNAME = ?;" if x is not None else "" for x in size_vars]
    ))
    declare = '\n'.join(['\n'.join(x) for x in declare])

    # develop syntax for SQL table creation
    if sql_primary_key and primary_key_column is not None:
        raise ValueError('if sql_primary_key==True then primary_key_column has to be None')
    syntax = list(zip(
        ["QUOTENAME(@ColumnName_"+x+")" for x in alias_names],
        ["QUOTENAME(@ColumnType_"+x+")" for x in alias_names],
        ["@ColumnSize_"+x+"" if x is not None else "" for x in size_vars],
        ["'NOT NULL'" if x in not_null else "" for x in column_names],
        ["'PRIMARY KEY'" if x==primary_key_column else "" for x in column_names]
    ))
    syntax = "+','+\n".join(
        ["+' '+".join([x for x in col if len(x)>0]) for col in syntax]
    )

    if sql_primary_key:
        syntax = "'_pk INT NOT NULL IDENTITY(1,1) PRIMARY KEY,'+\n"+syntax

    # develop syntax for sp_executesql parameters
    parameters = list(zip(
        ["@ColumnName_"+x+" SYSNAME" for x in alias_names],
        ["@ColumnType_"+x+" SYSNAME" for x in alias_names],
        ["@ColumnSize_"+x+" VARCHAR(MAX)" if x is not None else "" for x in size_vars]
    ))
    parameters = [", ".join([item for item in sublist if len(item)>0]) for sublist in parameters]
    parameters = ", ".join(parameters)

    # create input for sp_executesql SQL syntax
    values = list(zip(
        ["@ColumnName_"+x+""+"=@ColumnName_"+x+"" for x in alias_names],
        ["@ColumnType_"+x+""+"=@ColumnType_"+x+"" for x in alias_names],
        ["@ColumnSize_"+x+""+"=@ColumnSize_"+x+"" if x is not None else "" for x in size_vars]
    ))
    values = [", ".join([item for item in sublist if len(item)>0]) for sublist in values]
    values = ", ".join(values)

    # join components into final synax
    statement = statement.format(
        declare=declare,
        syntax=syntax,
        parameters=parameters,
        values=values
    )

    # create variables for execute method
    args = list(zip(
        [x for x in column_names],
        [x for x in dtypes],
        [x for x in size]
    ))
    args = [item for sublist in args for item in sublist if item is not None]
    args = [table_name] + args

    # execute statement
    connection.cursor.execute(statement, *args)


def __table_schema(schema): 
    '''Convert output from helpers.get_schema to inputs for table function.'''
    
    schema = schema.copy()

    # determine column's value
    schema[['max_length','precision','scale']] = schema[['max_length','precision','scale']].astype('str')
    schema['value'] = schema['data_type']
    # length
    dtypes = ['varchar','nvarchar']
    idx = schema['data_type'].isin(dtypes)
    schema.loc[idx, 'value'] = schema.loc[idx, 'value']+'('+schema.loc[idx,'max_length']+')'
    # precision & scale
    dtypes = ['decimal','numeric']
    idx = schema['data_type'].isin(dtypes)
    schema.loc[idx, 'value'] = schema.loc[idx, 'value']+'('+schema.loc[idx,'precision']+','+schema.loc[idx,'scale']+')'

    columns = schema['value'].to_dict()
    
    
    # non-null columns
    not_null = list(schema[~schema['is_nullable']].index)

    # primary_key_column/sql_primary_key
    primary_key_column = None
    sql_primary_key = False
    if sum(schema['is_identity'] & schema['is_primary_key'])==1:
        sql_primary_key = True
    elif sum(schema['is_primary_key'])==1:
        primary_key_column = schema[schema['is_primary_key']].index[0]

    return columns, not_null, primary_key_column, sql_primary_key


def from_dataframe(connection, table_name: str, dataframe: pd.DataFrame, primary_key : Literal[None,'sql','index','infer'] = None, 
row_count: int = 1000):
    """ Create SQL table by inferring SQL create table parameters from the contents of the DataFrame. 
    After table creation, the DataFrame values are inserted into the table.

    Parameters
    ----------

    connection (mssql_dataframe.connect) : connection for executing statement
    table_name (str) : name of table
    dataframe (DataFrame) : data used to create table
    primary_key (str, default = 'sql') : method of setting the table's primary key, see below for description of options
    row_count (int, default = 1000) : number of rows for determining data types

    primary_key = None : do not set a primary key
    primary_key = 'sql' : create an SQL managed auto-incrementing identity primary key column named '_pk'
    primary_key = 'index' : use the index of the dataframe and it's name, or '_index' if the index is not named
    primary_key = 'infer' : determine the column in the dataframe that best serves as a primary key and use it's name

    Returns
    -------

    None

    """

    options = [None,'sql','index','infer']
    if primary_key not in options:
        raise ValueError("primary_key must be one of: "+str(options))

    # # assume initial default data type
    # columns = {x:'NVARCHAR(MAX)' for x in dataframe.columns}

    # determine primary key
    if primary_key is None:
        sql_primary_key = False
        primary_key_column = None
    elif primary_key == 'sql':
        sql_primary_key = True
        primary_key_column = None
    elif primary_key == 'index':
        sql_primary_key = False
        if dataframe.index.name is None:
            dataframe.index.name = '_index'
        # # use the max allowed size for a primary key
        # columns[dataframe.index.name] = 'NVARCHAR(450)'
        primary_key_column = dataframe.index.name
        dataframe = dataframe.reset_index()
    elif primary_key == 'infer':
        sql_primary_key = False
        primary_key_column = None

    # not_null columns
    not_null = list(dataframe.columns[dataframe.notna().all()])

    # create temp table to determine data types
    name_temp = "##from_dataframe_"+table_name
    # table(connection, name_temp, columns, not_null=not_null, primary_key_column=primary_key_column, sql_primary_key=None)

    # insert data into temp table to determine datatype
    # subset = dataframe.loc[0:row_count, :]
    # datetimes = subset.select_dtypes('datetime').columns
    # numeric = subset.select_dtypes(include=np.number).columns
    # subset = subset.astype('str')
    # for col in subset:
    #     subset[col] = subset[col].str.strip()
    # # # truncate datetimes to 3 decimal places
    # subset[datetimes] = subset[datetimes].replace(r'(?<=\.\d{3})\d+','', regex=True)
    # # # remove zero decimal places from numeric values
    # subset[numeric] = subset[numeric].replace(r'\.0+','', regex=True)
    # # # treat empty like as None (NULL in SQL)
    # subset = subset.replace({'': None, 'None': None, 'nan': None, 'NaT': None})
    # # insert subset of data then use SQL to determine SQL data type
    # write.insert(connection, table_name=name_temp, dataframe=subset)
    # dtypes = helpers.infer_datatypes(connection, table_name=name_temp, column_names=dataframe.columns)
    dtypes = helpers.infer_datatypes(connection, name_temp, dataframe, row_count)

    # # determine length of VARCHAR columns
    # length = [k for k,v in dtypes.items() if v=="VARCHAR"]
    # length = subset[length].apply(lambda x: x.str.len()).max().astype('Int64')
    # length = {k:"VARCHAR("+str(v)+")" for k,v in length.items()}
    # dtypes.update(length)

    # infer primary key column after best fit data types have been determined
    if primary_key=='infer':
        # primary key must not be null
        subset = dataframe[not_null]
        # primary key must contain unique values
        unique = subset.nunique()==len(subset)
        unique = unique[unique].index
        subset = subset[unique]
        # attempt to use smallest integer
        primary_key_column = subset.select_dtypes(['int16', 'int32', 'int64']).columns
        if len(primary_key_column)>0:
            primary_key_column = subset[primary_key_column].max().idxmin()
        else:
            # attempt to use smallest float
            primary_key_column = subset.select_dtypes(['float16', 'float32', 'float64']).columns
            if len(primary_key_column)>0:
                primary_key_column = subset[primary_key_column].max().idxmin()
            else:
                # attempt to use shortest length string
                primary_key_column = subset.select_dtypes(['object']).columns
                if len(primary_key_column)>0:
                    primary_key_column = subset[primary_key_column].apply(lambda x: x.str.len()).max().idxmin()
                else:
                    primary_key_column = None    

    # create final SQL table then insert data
    table(connection, table_name, dtypes, not_null=not_null, primary_key_column=primary_key_column, sql_primary_key=sql_primary_key)
    write.insert(connection, table_name, dataframe)