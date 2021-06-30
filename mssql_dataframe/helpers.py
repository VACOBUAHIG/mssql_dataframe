import re

import pandas as pd
import numpy as np

from mssql_dataframe import errors
from mssql_dataframe import write
from mssql_dataframe import create


def safe_sql(connection, inputs):
    ''' Sanitize a list of string inputs into safe object names.

    Parameters
    ----------

    connection (mssql_dataframe.connect) : connection for executing statement
    inputs (list|str) : list of strings to sanitize

    Returns
    -------

    clean (tuple) : santized strings

    '''
    
    flatten = False
    if isinstance(inputs, str):
        flatten = True
        inputs = [inputs]
    elif not isinstance(inputs, list):
        inputs = list(inputs)

    statement = "SELECT {syntax}"
    syntax = ", ".join(["QUOTENAME(?)"]*len(inputs))
    statement = statement.format(syntax=syntax)
    
    
    clean = connection.cursor.execute(statement, inputs).fetchone()
    # values too long with return None, so raise an exception
    if len([x for x in clean if x is None])>0:
        raise errors.GeneralError("GeneralError") from None
    
    if flatten:
        clean = clean[0]

    return clean


def where_clause(connection, where: str):
    ''' Safely format a where clause condition.

    Parameters
    ----------

    connection (mssql_dataframe.connect) : connection for executing statement
    where (str) : where conditions to apply

    Returns
    -------

    where_statement (str) : where statement containing parameters such as "...WHERE [username] = ?"
    where_args (list) : parameter values

    Example
    -------

    where_statement, where_args = where_clause(connection, 'ColumnA >5 AND ColumnB=2 and ColumnANDC IS NOT NULL')
    where_statement == 'WHERE [ColumnA] > ? AND [ColumnB] = ? and [ColumnANDC] IS NOT NULL'
    where_args == ['5','2']

    '''

    # regular expressions to parse where statement
    combine = r'\bAND\b|\bOR\b'
    comparison = ["=",">","<",">=","<=","<>","!=","!>","!<","IS NULL","IS NOT NULL"]
    comparison = r'('+'|'.join([x for x in comparison])+')'
    
    # split on AND/OR
    conditions = re.split(combine, where, flags=re.IGNORECASE)
    # split on comparison operator
    conditions = [re.split(comparison,x, flags=re.IGNORECASE) for x in conditions]
    if len(conditions)==1 and len(conditions[0])==1:
        raise errors.InvalidSyntax("invalid syntax for where = "+where)
    # form dict for each colum, while handling IS NULL/IS NOT NULL split
    conditions = [[y.strip() for y in x] for x in conditions]
    conditions = {x[0]:(x[1::] if len(x[2])>0 else [x[1]]) for x in conditions}

    # santize column names
    column_names =  safe_sql(connection, conditions.keys())
    column_names = dict(zip(conditions.keys(), column_names))
    conditions = dict((column_names[key], value) for (key, value) in conditions.items())
    conditions = conditions.items()

    # form SQL where statement
    where_statement = [x[0]+' '+x[1][0]+' ?' if len(x[1])>1 else x[0]+' '+x[1][0] for x in conditions]
    recombine = re.findall(combine, where, flags=re.IGNORECASE)+['']
    where_statement = list(zip(where_statement,recombine))
    where_statement = 'WHERE '+' '.join([x[0]+' '+x[1] for x in where_statement])
    where_statement = where_statement.strip()

    # form arguments, skipping IS NULL/IS NOT NULL
    where_args = {'param'+str(idx):x[1][1] for idx,x in enumerate(conditions) if len(x[1])>1}
    where_args = [x[1][1] for x in conditions if len(x[1])>1]

    return where_statement, where_args


def column_spec(columns: list):
    ''' Extract SQL data type, size, and precision from list of strings.

    Parameters
    ----------
    
    columns (list|str) : strings to extract SQL specifications from

    Returns
    -------

    size (list|str)

    dtypes (list|str)

    '''

    flatten = False
    if isinstance(columns,str):
        columns = [columns]
        flatten = True

    pattern = r"(\(\d+\)|\(\d.+\)|\(MAX\))"
    size = [re.findall(pattern, x) for x in columns]
    size = [x[0] if len(x)>0 else None for x in size]
    dtypes = [re.sub(pattern,'',var) for var in columns]

    if flatten:
        size = size[0]
        dtypes = dtypes[0]

    return size, dtypes


def infer_datatypes(connection, table_name: str, dataframe: pd.DataFrame, row_count: int = 1000):
    """ Dynamically determine SQL variable types by issuing a statement against a temporary SQL table.

    Parameters
    ----------

    connection (mssql_dataframe.connect) : connection for executing statement
    table_name (str) : name of temporary table to create
    dataframe (pandas.DataFrame) : data that needs data type inferred
    row_count (int, default = 1000) : number of rows for determining data types

    Returns
    -------

    dtypes (dict) : keys = column name, values = data types and optionally size


    """
    # create temporary table
    columns = {x:'NVARCHAR(MAX)' for x in dataframe.columns}
    create.table(connection, table_name, columns)
    
    # insert subset of data into temporary table
    subset = dataframe.loc[0:row_count, :]
    datetimes = subset.select_dtypes('datetime').columns
    numeric = subset.select_dtypes(include=np.number).columns
    subset = subset.astype('str')
    for col in subset:
        subset[col] = subset[col].str.strip()
    # # truncate datetimes to 3 decimal places
    subset[datetimes] = subset[datetimes].replace(r'(?<=\.\d{3})\d+','', regex=True)
    # # remove zero decimal places from numeric values
    subset[numeric] = subset[numeric].replace(r'\.0+','', regex=True)
    # # treat empty like as None (NULL in SQL)
    subset = subset.replace({'': None, 'None': None, 'nan': None, 'NaT': None, '<NA>': None})
    # insert subset of data then use SQL to determine SQL data type
    write.insert(connection, table_name, dataframe=subset)

    statement = """
    DECLARE @SQLStatement AS NVARCHAR(MAX);
    DECLARE @TableName SYSNAME = ?;
    {declare}
    SET @SQLStatement = N'
        SELECT ColumnName,
        (CASE 
            WHEN count(try_convert(BIT, _Column)) = count(_Column) 
                AND MAX(_Column)=1 AND count(_Column)>2 THEN ''BIT''
            WHEN count(try_convert(TINYINT, _Column)) = count(_Column) THEN ''TINYINT''
            WHEN count(try_convert(SMALLINT, _Column)) = count(_Column) THEN ''SMALLINT''
            WHEN count(try_convert(INT, _Column)) = count(_Column) THEN ''INT''
            WHEN count(try_convert(BIGINT, _Column)) = count(_Column) THEN ''BIGINT''
            WHEN count(try_convert(TIME, _Column)) = count(_Column) 
                AND SUM(CASE WHEN try_convert(DATE, _Column) = ''1900-01-01'' THEN 0 ELSE 1 END) = 0
                THEN ''TIME''
            WHEN count(try_convert(DATETIME, _Column)) = count(_Column) THEN ''DATETIME''
            WHEN count(try_convert(FLOAT, _Column)) = count(_Column) THEN ''FLOAT''
            ELSE ''VARCHAR''
        END) AS type
        FROM '+QUOTENAME(@TableName)+'
        CROSS APPLY (VALUES
            {syntax}
        ) v(ColumnName, _Column)
        WHERE _Column IS NOT NULL
        GROUP BY ColumnName;'
    EXEC sp_executesql 
    @SQLStatement,
    N'@TableName SYSNAME, {parameters}',
    @TableName=@TableName, {values};
    """

    column_names = list(dataframe.columns)
    alias_names = [str(x) for x in list(range(0,len(column_names)))]

    # develop syntax for SQL variable declaration
    declare = list(zip(
        ["DECLARE @ColumnName_"+x+" SYSNAME = ?;" for x in alias_names]
    ))
    declare = "\n".join(["\n".join(x) for x in declare])

    # develop syntax for determine data types
    syntax = list(zip(
        ["''Column"+x+"''" for x in alias_names],
        ["+QUOTENAME(@ColumnName_"+x+")+" for x in alias_names]
    ))
    syntax = ",\n".join(["\t("+x[0]+", '"+x[1]+"')" for x in syntax])

    # develop syntax for sp_executesql parameters
    parameters = ", ".join(["@ColumnName_"+x+" SYSNAME" for x in alias_names])

    # create input for sp_executesql SQL syntax
    values = ", ".join(["@ColumnName_"+x+""+"=@ColumnName_"+x+"" for x in alias_names])

    # join components into final synax
    statement = statement.format(
        declare=declare,
        syntax=syntax,
        parameters=parameters,
        values=values
    )

    # create variables for execute method
    args = [table_name] + column_names

    # execute statement
    dtypes = connection.cursor.execute(statement, *args).fetchall()
    dtypes = [x[1] for x in dtypes]
    dtypes = list(zip(column_names,dtypes))
    dtypes = {x[0]:x[1] for x in dtypes}

    # determine length of VARCHAR columns
    length = [k for k,v in dtypes.items() if v=="VARCHAR"]
    length = subset[length].apply(lambda x: x.str.len()).max().astype('Int64')
    length = {k:"VARCHAR("+str(v)+")" for k,v in length.items()}
    dtypes.update(length)

    return dtypes


def read_query(connection, statement: str, arguments: list = None) -> pd.DataFrame:
    
    if arguments is None:
         dataframe = connection.cursor.execute(statement)
    else:
        dataframe = connection.cursor.execute(statement, *arguments)
    dataframe = dataframe.fetchall()
    dataframe = [list(x) for x in dataframe]

    # form dataframe with column names
    columns = [col[0] for col in connection.cursor.description]
    dataframe = pd.DataFrame(dataframe, columns=columns)

    return dataframe


def get_schema(connection, table_name: str):
    ''' Get SQL schema of a table.

    Parameters
    ----------

    connection (mssql_dataframe.connect) : connection for executing statement
    table_name (str) : name of table to retrieve schema of

    Returns
    -------
    schema (pandas.DataFrame) : schema for each column in the table

    '''

    table_name = safe_sql(connection, table_name)

    statement = """
    SELECT
        sys.columns.name AS column_name,
        TYPE_NAME(SYSTEM_TYPE_ID) AS data_type, 
        sys.columns.max_length, 
        sys.columns.precision, 
        sys.columns.scale, 
        sys.columns.is_nullable, 
        sys.columns.is_identity,
        sys.indexes.is_primary_key
    FROM sys.columns
    LEFT JOIN sys.index_columns
        ON sys.index_columns.object_id = sys.columns.object_id 
        AND sys.index_columns.column_id = sys.columns.column_id
    LEFT JOIN sys.indexes
        ON sys.indexes.object_id = sys.index_columns.object_id 
        AND sys.indexes.index_id = sys.index_columns.index_id
    WHERE sys.columns.object_ID = OBJECT_ID('{table_name}')
    """

    statement = statement.format(table_name=table_name)

    schema = read_query(connection, statement)
    if len(schema)==0:
         raise errors.TableDoesNotExist('{table_name} does not exist'.format(table_name=table_name)) from None
    
    schema = schema.set_index('column_name')
    schema['is_primary_key'] = schema['is_primary_key'].fillna(False)


    # define Python type equalivant
    equal = pd.DataFrame.from_dict({
        'varchar': ['object'],
        'bit': ['boolean'],
        'tinyint': ['Int8'],
        'smallint': ['Int16'],
        'int': ['Int32'],
        'bigint': ['Int64'],
        'float': ['float64'],
        'decimal': ['float64'],
        'time': ['timedelta64[ns]'],
        'date': ['datetime64[ns]'],
        'datetime': ['datetime64[ns]'],
        'datetime2': ['datetime64[ns]']
    }, orient='index', columns=["python_type"])
    schema = schema.merge(equal, left_on='data_type', right_index=True, how='left')
    if any(schema['python_type'].isna()):
        raise errors.UndefinedPythonDataType("SQL Columns: "+str(list(schema[schema['python_type'].isna()].index)))

    return schema