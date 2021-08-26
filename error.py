import pandas as pd
import numpy as np
import pyodbc
import struct
import time
import warnings

# database connection and cursor
connection = pyodbc.connect(
    driver='ODBC Driver 17 for SQL Server', server='localhost', database='master',
    autocommit=False, trusted_connection='yes'
)
cursor = connection.cursor()
cursor.fast_executemany = True

# SQL TIME vs. pandas timedelta64[ns]
## SQL limits range from '00:00:00.0000000' to '23:59:59.9999999' while pandas allows multiple days and negatives
## SQL limits precision to 7 decimal places while pandas allows 9

# SQL DATE vs pandas datetime64[ns]
## pandas limits range from '1677-09-21' to '2262-04-11' while SQL allows '0001-01-01' through '9999-12-31'

# SQL DATETIME2 vs. pandas datetime64[ns]
## pandas limits range from '1677-09-21 00:12:43.145225' to '2262-04-11 23:47:16.854775807' while SQL allows '0001-01-01' through '9999-12-31'
## SQL limits precision to 7 decimal places while pandas allows 9

# SQL DECIMAL & SQL NUMERIC
## pandas does not support exact decimal numerics

# conversion rules
## {'sql': <SQL_data_type>, 'pandas': <pandas_data_type>, 'odbc': <odbc_data_type>, 'size': <SQL_size>, 'precision': <SQL_precision>}
conversion = pd.DataFrame.from_records([
    # {'sql': 'bit', 'pandas': 'boolean', 'odbc': pyodbc.SQL_BIT, 'size': 1, 'precision': 0},
    # {'sql': 'tinyint', 'pandas': 'UInt8', 'odbc': pyodbc.SQL_TINYINT, 'size': 1, 'precision': 0},
    # {'sql': 'smallint', 'pandas': 'Int16', 'odbc': pyodbc.SQL_SMALLINT, 'size': 2, 'precision': 0},
    # {'sql': 'int', 'pandas': 'Int32', 'odbc': pyodbc.SQL_INTEGER, 'size': 4, 'precision': 0},
    {'sql': 'bigint', 'pandas': 'Int64', 'odbc': pyodbc.SQL_BIGINT, 'size': 8, 'precision': 0},
    # {'sql': 'float', 'pandas': 'float64', 'odbc': pyodbc.SQL_FLOAT, 'size': 8, 'precision': 53},
    # {'sql': 'time', 'pandas': 'timedelta64[ns]', 'odbc': pyodbc.SQL_SS_TIME2, 'size': 16, 'precision': 7},
    {'sql': 'date', 'pandas': 'datetime64[ns]', 'odbc': pyodbc.SQL_TYPE_DATE, 'size': 10, 'precision': 0},
    {'sql': 'datetime2', 'pandas': 'datetime64[ns]', 'odbc': pyodbc.SQL_TYPE_TIMESTAMP, 'size': 27, 'precision': 7},
    # {'sql': 'varchar', 'pandas': 'string', 'odbc': pyodbc.SQL_VARCHAR},
    # {'sql': 'nvarchar', 'pandas': 'string', 'odbc': pyodbc.SQL_WVARCHAR},
])

# sample data
## format >> {'_<SQL_data_type>': pd.Series([<lower_limit>,<upper_limit>,<null>,<truncation>],dtype=<pandas_data_type>)}
sample = pd.DataFrame({
    # '_bit': pd.Series([False, True, None], dtype='boolean'),
    # '_tinyint': pd.Series([0, 255, None], dtype='UInt8'),
    # '_smallint': pd.Series([-2**15, 2**15-1, None], dtype='Int16'),
    # '_int': pd.Series([-2**31, 2**31-1, None], dtype='Int32'),
    '_bigint': pd.Series([-2**63, 2**63-1, None], dtype='Int64'),
    # '_float': pd.Series([-1.79**308, 1.79**308, None], dtype='float'),
    # '_time': pd.Series(['00:00:00.0000000','23:59:59.9999999',None], dtype='timedelta64[ns]'),
    '_date': pd.Series([(pd.Timestamp.min+pd.DateOffset(days=1)).date(), pd.Timestamp.max.date(), None], dtype='datetime64[ns]'),
    '_datetime2': pd.Series([pd.Timestamp.min, pd.Timestamp.max, None], dtype='datetime64[ns]'),
})
## increase sample size rows
# sample = pd.concat([sample]*10000).reset_index(drop=True)
## add id column for return sorting
sample.index.name = 'id'
sample = sample.reset_index()
sample['id'] = sample['id'].astype('Int64')

# insure conversion is fully defined
missing = conversion.isna().any()
if any(missing):
    missing = missing[missing].index.tolist()
    raise AttributeError(f'conversion is not fully defined for: {missing}')

# insure full testing
## insure all conversion rules are being tested
missing = ~conversion['sql'].isin([x[1::] for x in sample.columns])
if any(missing):
    missing = conversion.loc[missing,'sql'].tolist()
    raise AttributeError(f'columns missing from sample dataframe, SQL column names: {missing}')
## insure correct pandas types are being tested
check = pd.Series(sample.dtypes, name='sample')
check = check.apply(lambda x: x.name)
check.index = [x[1::] for x in check.index]
check = conversion.merge(check, left_on='sql', right_index=True)
wrong = check['pandas']!=check['sample']
if any(wrong):
    wrong = check.loc[wrong,'sql'].tolist()
    raise AttributeError(f'columns with wrong data type in sample dataframe, SQL column names: {wrong}')

# table creation
create = ',\n'.join([x+' '+x[1::].upper() for x in sample.columns if x!='id'])
create = f"""
CREATE TABLE ##conversion (
id BIGINT PRIMARY KEY,
{create}
)"""
cursor.execute(create)

# prepare write parameters
## example: cursor.setinputsizes([(<ODBC_data_type>, <SQL_size>, <SQL_precision>)])
## example: cursor.setinputsizes([(pyodbc.SQL_BIT, 1, 0), (pyodbc.SQL_SS_TIME2, 16, 7)])
query_params = pd.DataFrame(pd.Series([x.name for x in sample.dtypes], name='pandas'))
missing = ~query_params['pandas'].isin(conversion['pandas'])
if any(missing):
    missing = query_params.loc[missing,'pandas'].unique().tolist()
    raise AttributeError(f'undefined conversion for pandas data types: {missing}')    
query_params = query_params.merge(conversion, how='left')
query_params = query_params[['odbc','size','precision']].to_numpy().tolist()
query_params = [tuple(x) for x in query_params]
cursor.setinputsizes(query_params)

# prepare write values
prepped = sample.copy()
## SQL data type TIME
### as string since datetime.time allows 6 decimal places but SQL allows 7
dtype = prepped.select_dtypes('timedelta64[ns]').columns
truncation = prepped[dtype].apply(lambda x: any(x.dt.nanoseconds%100>0))
if any(truncation):
    truncation = list(truncation[truncation].index)
    warnings.warn(f'nanosecond precision for columns {truncation} will be truncated as TIME allows 7 max decimal places')
invalid = ((prepped[dtype]>=pd.Timedelta(days=1)) | (prepped[dtype]<pd.Timedelta(days=0))).any()
if any(invalid):
    invalid = list(invalid[invalid].index)
    raise ValueError(f'columns {invalid} are out of range for SQL TIME data type. Allowable range is 00:00:00.0000000-23:59:59.9999999')
prepped[dtype] = prepped[dtype].astype('str')
prepped[dtype] = prepped[dtype].replace({'NaT': None})
prepped[dtype] = prepped[dtype].apply(lambda x: x.str[7:23])
## SQL data type DATETIME2
### as string since datetime.datetime allows 6 decimals but SQL allows 7
dtype = prepped.select_dtypes('datetime64[ns]').columns
truncation = prepped[dtype].apply(lambda x: any(x.dt.nanosecond%100>0))
if any(truncation):
    truncation = list(truncation[truncation].index)
    warnings.warn(f'nanosecond precision for columns {truncation} will be truncated as DATETIME2 allows 7 max decimal places')
prepped[dtype] = prepped[dtype].astype('str')
prepped[dtype] = prepped[dtype].replace({'NaT': None})
prepped[dtype] = prepped[dtype].apply(lambda x: x.str[0:27])
## treat NA for any pandas data type as NULL in SQL
prepped = prepped.fillna(np.nan).replace([np.nan], [None])

# insert data
insert = ', '.join(sample.columns)
values = ', '.join(['?']*len(sample.columns))
statement = f"""
INSERT INTO
##conversion (
    {insert}
) VALUES (
    {values}
)
"""
args = prepped.values.tolist()
cursor.executemany(statement, args)
cursor.commit()

# prepare for reading
## nullable type with increased precision over datetime.time()
def SQL_SS_TIME2(raw_bytes, pattern=struct.Struct("<4hI")):
    hour, minute, second, _, fraction = pattern.unpack(raw_bytes)
    return pd.Timedelta(hours=hour, minutes=minute, seconds=second, microseconds=fraction//1000, nanoseconds=fraction%1000)
connection.add_output_converter(pyodbc.SQL_SS_TIME2, SQL_SS_TIME2)
## nullable type with increased precision over datetime.datetime()
def SQL_TYPE_TIMESTAMP(raw_bytes, pattern=struct.Struct("hHHHHHI")):
    year, month, day, hour, minute, second, fraction = pattern.unpack(raw_bytes)
    return pd.Timestamp(year=year, month=month, day=day, hour=hour, minute=minute, second=second, microsecond=fraction//1000, nanosecond=fraction%1000)
connection.add_output_converter(pyodbc.SQL_TYPE_TIMESTAMP, SQL_TYPE_TIMESTAMP)

# read data
time_start = time.time()
select = ', '.join([x for x in sample.columns if x!='id'])
result = cursor.execute(f'SELECT {select} FROM ##conversion ORDER BY id ASC').fetchall()
columns = [col[0] for col in cursor.description]
print('read in {} seconds'.format(time.time()-time_start))

# SQL schema for pandas data types
schema = []
for col in columns:
    x = list(list(cursor.columns(table='##conversion',catalog='tempdb',column=col).fetchone()))
    schema.append(x)
schema = pd.DataFrame(schema, columns = [x[0] for x in cursor.description])
dtypes = schema[['column_name','data_type']].merge(conversion[['odbc','pandas']], left_on='data_type', right_on='odbc')
dtypes = dtypes[['column_name','pandas']].to_numpy()
dtypes = {x[0]:x[1] for x in dtypes}

# form output using SQL schema and explicit pandas types
time_start = time.time()
result = {col: [row[idx] for row in result] for idx,col in enumerate(columns)}
result = {col: pd.Series(vals, dtype=dtypes[col]) for col,vals in result.items()}
result = pd.DataFrame(result)
print('dataframe in {} seconds'.format(time.time()-time_start))

# test result, accounting for precision differences
expected = sample.drop(columns=['id'])
expected['_datetime2'] = expected['_datetime2'].dt.floor('U')
assert result.equals(expected)