from __future__ import annotations
from collections import defaultdict
from datetime import date, datetime, time, timedelta
import re
import time as time2
from ._lib4d import ffi, lib

# DB-API 2.0 metadata
apilevel = "2.0"
threadsafety = 0
paramstyle = "pyformat"

# Error Classes
class Warning(Exception):
    pass

class Error(Exception):
    pass

class InterfaceError(Error):
    pass

class DatabaseError(Error):
    pass

class DataError(DatabaseError):
    pass

class OperationalError(DatabaseError):
    pass

class IntegrityError(DatabaseError):
    pass

class InternalError(DatabaseError):
    pass

class ProgrammingError(DatabaseError):
    pass

class NotSupportedError(DatabaseError):
    pass

# Data type classes
Date = date
Time = time
Timestamp = datetime
Binary = bytes

def DateFromTicks(ticks):
    return Date(*time2.localtime(ticks)[:3])

def TimeFromTicks(ticks):
    return Time(*time2.localtime(ticks)[3:6])

def TimestampFromTicks(ticks):
    return Timestamp(*time2.localtime(ticks)[:6])

# Cursor Object
class Cursor:
    """4D DB API 2.0 Cursor"""
    arraysize = 1
    pagesize = 100

    def __init__(self, connection):
        self.connection = connection
        # cffi state
        self._connptr = connection._connptr
        self._lib = connection._lib
        self._ffi = connection._ffi
        # curse state
        self._statement = None
        self._result = None
        # db state
        self._rowcount = -1
        self._description = None
        self._rownumber = None
        # interals
        self._prepared = None
        self._closed = None

    @property
    def rownumber(self):
        """"""
        return self._rownumber

    @property
    def description(self):
        """"""
        return self._description

    @property
    def rowcount(self):
        """"""
        return self._rowcount

    def setinputsizes(self):
        """"""
        pass

    def setoutputsize(self):
        """"""
        pass

    def _check_self(self):
        """Before you wreck self"""
        if self._closed:
            raise InterfaceError("Cursor is already closed")
        if not self.connection.connected:
            raise InternalError("Database not connected")

    def _free_result(self):
        if self._result is not None and self._result != self._ffi.NULL:
            self._lib.fourd_free_result(self._result)
            self._result = None

    def _free_statement(self):
        if self._statement is not None and self._statement != self._ffi.NULL:
            self._lib.fourd_free_statement(self._statement)
            self._statement = None

    def close(self):
        self._free_result()
        self._free_statement()
        self._description = None
        self._rowcount = -1
        self._rownumber = None
        self._resulttype = None
        self._closed = True

    def execute(self, query, params=None, describe=True):
        """Prepare and execute a database operation"""
        self._check_self()
        if params is None:
            params = ()
        # Normalize DB-API parameters
        if isinstance(params, dict):
            regex = re.compile(r'%\(([^\)]+)\)s')
            keys = re.findall(regex, query)
            if keys:
                params = [params[key] for key in keys]
                query = re.sub(regex, '?', query)
            else:
                regex = re.compile(r':([A-Za-z0-9_]+)')
                keys = re.findall(regex, query)
                if keys:
                    params = [params[key] for key in keys]
                    query = re.sub(regex, '?', query)
        # pyformat -> qmark
        query = re.sub(r'%[A-Za-z]', '?', query)
        query = query.replace('%%', '%')
        # Expand sequence parameters
        expanded_params = []
        for param in params:
            if isinstance(param, (tuple, list)):
                placeholders = ",".join("?" for _ in param)
                query = query.replace("?", f"({placeholders})", 1)
                expanded_params.extend(param)
            else:
                expanded_params.append(param)
        params = expanded_params
        # Transaction management
        if not self.connection.in_transaction:
            self.connection._start_transaction()
        # Prepare statement
        if not self._prepared:
            if self._statement is not None and self._statement != self._ffi.NULL:
                self._lib.fourd_free_statement(self._statement)
                self._statement = None
            self._statement = self._lib.fourd_prepare_statement( self._connptr, query.encode("utf-8"))
        if self._statement == self._ffi.NULL:
            raise ProgrammingError( self._ffi.string( self._lib.fourd_error(self._connptr)))
        # Bind parameters
        fourdtypes = defaultdict(
            lambda: self._lib.VK_STRING,
            {str: self._lib.VK_STRING, bool: self._lib.VK_BOOLEAN, int: self._lib.VK_LONG, float: self._lib.VK_REAL}
        )
        for idx, parameter in enumerate(params):
            param_type = type(parameter)
            fourd_type = fourdtypes[param_type]
            cleanup = False
            if isinstance(parameter, str):
                value = parameter.encode("UTF-16LE")
                cparam = self._lib.fourd_create_string( value, len(parameter))
                cleanup = True
            elif isinstance(parameter, bool):
                cparam = self._ffi.new( "FOURD_BOOLEAN *", parameter)
            elif isinstance(parameter, int):
                cparam = self._ffi.new( "FOURD_LONG *", parameter)
            elif isinstance(parameter, float):
                cparam = self._ffi.new( "FOURD_REAL *", parameter)
            elif parameter is None:
                cparam = self._ffi.NULL
            elif isinstance(parameter, time):
                value = parameter.strftime("%H:%M:%S").encode("UTF-16LE")
                cparam = self._lib.fourd_create_string( value, len(value))
                cleanup = True
            else:
                value = str(parameter).encode("UTF-16LE")
                cparam = self._lib.fourd_create_string( value, len(value))
                cleanup = True
            bound = self._lib.fourd_bind_param(self._statement, idx, fourd_type, cparam)
            if cleanup:
                self._lib.free(cparam.data)
                self._lib.free(cparam)
            if bound != 0:
                raise ProgrammingError( self._ffi.string(self._lib.fourd_error(self._connptr)))
        # Execute
        self._free_result()
        self._result = self._lib.fourd_exec_statement(self._statement, self.pagesize)
        if self._result == self._ffi.NULL:
            raise ProgrammingError(self._ffi.string( self._lib.fourd_error(self._connptr)))
        # Update cursor state
        self._resulttype = self._result.resultType
        if self._resulttype == self._lib.RESULT_SET:
            self._rowcount = self._lib.fourd_num_rows(self._result)
        elif self._resulttype == self._lib.UPDATE_COUNT:
            self._rowcount = self._lib.fourd_affected_rows(self._connptr)
        else:
            self._rowcount = -1
        self._rownumber = -1
        if describe:
            self._describe()
    #----------------------------------------------------------------------
    def _describe(self):
        """Populate the DB-API description attribute."""
        if self._result == self._ffi.NULL or self._result is None:
            return
        python_types = {
            self._lib.VK_BOOLEAN: bool,
            self._lib.VK_BYTE: str,
            self._lib.VK_WORD: str,
            self._lib.VK_LONG: int,
            self._lib.VK_LONG8: int,
            self._lib.VK_REAL: float,
            self._lib.VK_FLOAT: float,
            self._lib.VK_TIME: time,
            self._lib.VK_TIMESTAMP: datetime,
            self._lib.VK_DURATION: timedelta,
            self._lib.VK_TEXT: str,
            self._lib.VK_STRING: str,
            self._lib.VK_BLOB: Binary,
            self._lib.VK_IMAGE: Binary,
        }
        description = []
        for column in range(self._lib.fourd_num_columns(self._result)):
            name = self._lib.fourd_get_column_name(self._result, column)
            type_code = self._lib.fourd_get_column_type(self._result, column)
            try:
                python_type = python_types[type_code]
            except KeyError:
                raise OperationalError(f"Unrecognized 4D type: {type_code} in column: {column} with name: {name}")
            description.append((self._ffi.string(name).decode("utf-8"), python_type, None, None, None, None, None))
        self._description = description
    #----------------------------------------------------------------------
    def executemany(self, query, params):
        """Execute the same operation for multiple parameter sets."""
        self._check_self()
        first = True
        try:
            for paramlist in params:
                self._free_result()
                self.execute(query, paramlist, describe=False)
                if first:
                    first = False
                else: 
                    self._lib.fourd_close_statement(self._statement)
                self._prepared = True
            self._describe()
        finally:
            self._prepared = False
    #----------------------------------------------------------------------
    def fetchone(self):
        """Fetch the next row from the current result set."""
        self._check_self()
        if self._resulttype is None:
            raise DataError("No rows to fetch")
        if self._rowcount == 0 or self._resulttype == self._lib.UPDATE_COUNT:
            return None
        if self._lib.fourd_next_row(self._result) == 0:
            return None
        self._rownumber = self._result.numRow
        row = []
        column_count = self._lib.fourd_num_columns(self._result)
        strlen = self._ffi.new("size_t *")
        inbuff = self._ffi.new("char *[1]")
        for column in range(column_count):
            field_type = self._lib.fourd_get_column_type( self._result, column)
            if self._lib.fourd_field(self._result, column) == self._ffi.NULL:
                row.append(None)
                continue
            convert_result = self._lib.fourd_field_to_string( self._result, column, inbuff, strlen)
            strdata = inbuff[0]
            output = self._ffi.buffer(strdata, strlen[0])[:]
            if strdata != self._ffi.NULL and convert_result == 1:
                self._lib.free(strdata)
            if field_type in (self._lib.VK_STRING, self._lib.VK_TEXT):
                row.append(output.decode("UTF-16LE", errors="replace"))
            elif field_type == self._lib.VK_BOOLEAN:
                value = self._lib.fourd_field_long(self._result, column)
                row.append(bool(value[0]))
            elif field_type in (self._lib.VK_LONG, self._lib.VK_LONG8, self._lib.VK_WORD):
                value = self._lib.fourd_field_long(self._result, column)
                row.append(value[0])
            elif field_type in (self._lib.VK_REAL, self._lib.VK_FLOAT):
                row.append(None if output == b"" else float(output))
            elif field_type == self._lib.VK_TIMESTAMP:
                if output == b"0000/00/00 00:00:00.000":
                    row.append(None)
                else:
                    try:
                        row.append(
                            datetime(
                                int(output[:4]), int(output[5:7]),
                                int(output[8:10]), int(output[11:13]),
                                int(output[14:16]), int(output[17:19]),
                                int(output[20:23]) * 1000
                            )
                        )
                    except ValueError:
                        row.append(None)
            elif field_type == self._lib.VK_DURATION:
                value = self._lib.fourd_field_long(self._result, column)
                duration = timedelta(milliseconds=value[0])
                # Is this good? Maybe this SHOULD be an error...
                max_durr = timedelta(days=1) - timedelta(microseconds=1)
                row.append((datetime.min + max(duration, max_durr)).time())
            elif field_type in (self._lib.VK_BLOB, self._lib.VK_IMAGE):
                field = self._lib.fourd_field(self._result, column)
                if field == self._ffi.NULL:
                    row.append(None)
                else:
                    field = self._ffi.cast("FOURD_BLOB *", field)
                    data = self._ffi.buffer(field.data, field.length)[:]
                    row.append(Binary(data))
            else:
                row.append(output)
        return tuple(row)

    def fetchmany(self, size=None):
        """Fetch multiple rows."""
        self._check_self()
        if self._resulttype is None:
            raise DataError("No rows to fetch")
        if size is None:
            size = self.arraysize
        rows = []
        for _ in range(size):
            row = self.fetchone()
            if row is None:
                break
            rows.append(row)
        return rows

    def fetchall(self):
        """Fetch all remaining rows."""
        self._check_self()
        if self._resulttype is None:
            raise DataError("No rows to fetch")
        rows = []
        while True:
            row = self.fetchone()
            if row is None:
                break
            rows.append(row)
        return rows

    def __next__(self):
        row = self.fetchone()
        if row is None:
            raise StopIteration
        return row

    def __iter__(self):
        return self

    def __del__(self):
        self._free_statement()

    def __enter__(self):
        return self

    def __exit__(self, ex_type, ex_val, tb):
        self._free_statement()

# Connection object
class Connection:
    """4D DB API 2.0 Connection."""
    def __init__(self, host, user, password, database, port):
        self._ffi = ffi
        self._lib = lib
        self._connptr = self._lib.fourd_init()
        if self._connptr == self._ffi.NULL:
            raise InterfaceError("Unable to initialize connection object")
        self.connected = False
        self.in_transaction = False
        self.cursors = []
        result = self._lib.fourd_connect(
            self._connptr,
            host.encode("utf-8"),
            user.encode("utf-8"),
            password.encode("utf-8"),
            database.encode("utf-8"),
            port,
        )
        if result != 0:
            error = self._ffi.string( self._connptr.error_string)
            raise OperationalError( f"Unable to connect to 4D Server: {error}")
        self.connected = True
        self._private_cursor = self.cursor()

    def _start_transaction(self):
        if self.in_transaction:
            return
        self.in_transaction = True
        self._private_cursor.execute("START TRANSACTION")

    def close(self):
        """Close the database connection."""
        if self.in_transaction:
            self._private_cursor.execute("ROLLBACK")
            self.in_transaction = False
        for cursor in self.cursors:
            cursor.close()
        self.cursors.clear()
        if self.connected:
            result = self._lib.fourd_close(self._connptr)
            if result != 0:
                raise OperationalError(f"Failed to close connection: {result}")
            self._lib.fourd_free(self._connptr)
        self._connptr = None
        self.connected = False

    def commit(self):
        """Commit current transaction."""
        if self.in_transaction:
            self._private_cursor.execute("COMMIT")
            self.in_transaction = False

    def rollback(self):
        """Rollback current transaction."""
        if self.in_transaction:
            self._private_cursor.execute("ROLLBACK")
            self.in_transaction = False

    def cursor(self):
        cursor = Cursor(self)
        self.cursors.append(cursor)
        return cursor

    def __enter__(self):
        return self

    def __exit__(self, ex_type, ex_value, traceback):
        if ex_type is not None:
            self.rollback()
        else:
            self.commit()
        self.close()

def connect(dsn=None, user=None, password=None, host=None, database=None, port=None):
    """Create a 4D database connection."""
    connect_args = {}
    if dsn is not None:
        for part in dsn.split(";"):
            part = part.strip()
            if not part:
                continue
            key, value = part.split("=", 1)
            if key not in {"host", "user", "password", "database", "port"}:
                raise ValueError(f"Unrecognized parameter: {key}")
            connect_args[key] = value
    overrides = {"user": user, "password": password, "host": host, "database": database, "port": port}
    for key, value in overrides.items():
        if value is not None:
            connect_args[key] = value
    if "host" not in connect_args:
        raise ValueError("Host name is required")
    for key in ("user", "password", "database"):
        connect_args.setdefault(key, "")
    connect_args["port"] = int(connect_args.get("port", 19812))
    return Connection(**connect_args)
