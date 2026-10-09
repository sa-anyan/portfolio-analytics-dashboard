"""Bound uploaded content before pandas/openpyxl; retain original financial data."""
import csv
from io import StringIO, BytesIO
from pathlib import Path
import re
import zipfile
from xml.etree import ElementTree as ET

MAX_FILE_BYTES = 5 * 1024 * 1024
MAX_BATCH_BYTES = 20 * 1024 * 1024
MAX_FILES = 10
MAX_ROWS = 20_000
MAX_COLUMNS = 64
MAX_CELL_CHARS = 2048
MAX_SECURITIES = 100
MAX_ACCOUNTS = 50
MAX_EXPANDED_BYTES = 25 * 1024 * 1024


def validate_batch(files):
    if len(files) > MAX_FILES or sum(len(data) for _, data in files) > MAX_BATCH_BYTES:
        raise ValueError('Upload limit: at most 10 files and 20 MiB per draft.')
    for name, data in files:
        if len(data) > MAX_FILE_BYTES:
            raise ValueError('Upload limit: at most 5 MiB per file.')


def csv_text(data):
    try:
        text = data.decode('utf-8-sig', errors='strict')
        if '\x00' in text:
            raise ValueError()
        rows = csv.reader(StringIO(text), strict=True)
        header = next(rows)
        if not header or len(header) > MAX_COLUMNS:
            raise ValueError('Upload limit: at most 64 columns.')
        if len(set(header)) != len(header):
            raise ValueError('Duplicate CSV headers are ambiguous.')
        count = 0
        for row in rows:
            if not row:
                continue
            count += 1
            if count > MAX_ROWS or len(row) != len(header) or any(len(v)>MAX_CELL_CHARS for v in row):
                raise ValueError('CSV shape/cell limit exceeded: 20,000 rows, consistent columns, 2,048 characters per cell.')
        return text
    except (UnicodeError, csv.Error, StopIteration):
        raise ValueError('Malformed CSV; use strict UTF-8 and a rectangular table.') from None


def validate_xlsx(data):
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            entries = archive.infolist()
            names = [i.filename for i in entries]
            if len(entries)>200 or len(names)!=len(set(names)) or sum(i.file_size for i in entries)>MAX_EXPANDED_BYTES:
                raise ValueError('Workbook expansion limit exceeded.')
            if not {'[Content_Types].xml','xl/workbook.xml'} <= set(names):
                raise ValueError('Content is not an XLSX workbook.')
            for i in entries:
                name=i.filename.lower()
                if i.flag_bits & 1 or '..' in Path(name).parts or name.startswith('/') or i.file_size>MAX_EXPANDED_BYTES:
                    raise ValueError('Unsupported workbook archive content.')
                if any(v in name for v in ('vbaproject', 'externallinks/', 'embeddings/', 'activex/', 'connections.xml')):
                    raise ValueError('Macros, external links and embedded workbook objects are unsupported.')
                if name.endswith('.xml') or name.endswith('.rels'):
                    raw=archive.read(i)
                    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                        raise ValueError('Workbook XML entities are unsupported.')
                    root=ET.fromstring(raw)
                    for node in root.iter():
                        local=node.tag.rsplit('}',1)[-1]
                        if local=='f' or node.attrib.get('TargetMode')=='External':
                            raise ValueError('Workbook formulas and external references are unsupported; upload values only.')
                        if node.text and len(node.text)>MAX_CELL_CHARS:
                            raise ValueError('Workbook cell text is too long.')
                    if name.startswith('xl/worksheets/'):
                        cells=0; rows=0
                        for node in root.iter():
                            local=node.tag.rsplit('}',1)[-1]
                            if local=='row':
                                rows+=1
                                if rows>MAX_ROWS+1 or int(node.attrib.get('r','0'))>MAX_ROWS+1:
                                    raise ValueError('Workbook row limit exceeded.')
                            if local=='c':
                                cells+=1
                                letters=re.match(r'[A-Z]+',node.attrib.get('r',''))
                                column=0
                                for ch in letters.group() if letters else 'A': column=column*26+ord(ch)-64
                                if column>MAX_COLUMNS or cells>(MAX_ROWS+1)*MAX_COLUMNS:
                                    raise ValueError('Workbook column/cell limit exceeded.')
    except (zipfile.BadZipFile, ET.ParseError, KeyError, RuntimeError):
        raise ValueError('Malformed XLSX workbook.') from None


def validate_frame(frame, *, security_limit=MAX_SECURITIES):
    if len(frame)>MAX_ROWS or len(frame.columns)>MAX_COLUMNS:
        raise ValueError('Table exceeds the 20,000 row / 64 column limit.')
    for column in frame:
        key=re.sub(r'[^a-z0-9]','',str(column).lower())
        values=frame[column].dropna().astype(str)
        if values.map(len).gt(MAX_CELL_CHARS).any() or values.str.contains(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',regex=True).any():
            raise ValueError('Invalid control characters or oversized cell.')
        if key in {'ticker','symbol','constituentticker','parentticker','underlyingticker'}:
            if values.nunique()>security_limit or not values.map(lambda s: bool(re.fullmatch(r'[A-Za-z0-9.^=_:/-]{1,40}',s.strip())) or s.strip() in {'','nan','None'}).all():
                raise ValueError(f'Invalid security identifiers or more than {security_limit} securities.')
        if key in {'account','accountid','accountnumber','portfolioid'}:
            if values.nunique()>MAX_ACCOUNTS:
                raise ValueError('Upload limit: at most 50 accounts.')


def spreadsheet_safe(frame):
    """Presentation/export copy only; never mutate input/provenance or numeric values."""
    return frame.map(lambda v: "'"+v if isinstance(v,str) and v.lstrip().startswith(('=','+','-','@','\t','\r')) else v)
