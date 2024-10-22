import logging
import re
from io import BytesIO
from pathlib import Path
from typing import Set, Union

from docling_core.types.doc import (
    DocItemLabel,
    DoclingDocument,
    DocumentOrigin,
    GroupLabel,
    TableCell,
    TableData,
    ImageRef,
)

from docling.backend.abstract_backend import DeclarativeDocumentBackend
from docling.datamodel.base_models import InputFormat
from docling.datamodel.document import InputDocument

_log = logging.getLogger(__name__)


class AsciidocBackend(DeclarativeDocumentBackend):

    def __init__(self, in_doc: InputDocument, path_or_stream: Union[BytesIO, Path]):
        super().__init__(in_doc, path_or_stream)

        self.path_or_stream = path_or_stream

        self.valid = True

    def is_valid(self) -> bool:
        return self.valid

    @classmethod
    def supports_pagination(cls) -> bool:
        return False

    def unload(self):
        return

    @classmethod
    def supported_formats(cls) -> Set[InputFormat]:
        return {InputFormat.ASCIIDOC}

    def convert(self) -> DoclingDocument:
        """
        Parses the ASCII into a structured document model.
        """

        fname = ""
        if isinstance(self.path_or_stream, Path):
            fname = self.path_or_stream.name

        origin = DocumentOrigin(
            filename=fname,
            mimetype="text/asciidoc",
            binary_hash=self.document_hash,
        )
        if len(fname) > 0:
            docname = Path(fname).stem
        else:
            docname = "stream"

        doc = DoclingDocument(name=docname, origin=origin)

        doc = self.parse(doc)

        return doc

    def parse(self, doc: DoclingDocument):
        """
        Main function that orchestrates the parsing by yielding components:
        title, section headers, text, lists, and tables.
        """

        content = ""
        if isinstance(self.path_or_stream, Path):
            with open(self.path_or_stream, "r") as fr:
                self.lines = fr.readlines()

        # self.lines = file_content.splitlines()

        in_list = False
        in_table = False

        text_data = []
        table_data = []
        caption_data = []

        parents = {}
        indents = {}
        
        for i in range(0, 10):
            parents[i] = None
            indents[i] = None
            
        for line in self.lines:
            #line = line.strip()

            # Title
            if self.is_title(line):
                item = self.parse_title(line)
                level = item["level"]
                
                parents[level] = doc.add_text(text=item["text"], label=DocItemLabel.TITLE)
                
            # Section headers
            elif self.is_section_header(line):
                item = self.parse_section_header(line)
                level = item["level"]
                
                parents[level] = doc.add_heading(text=item["text"], level=item["level"], parent=parents[level-1])
                for k,v in parents.items():
                    if k>level:
                        parents[k] = None
                
            # Lists
            elif self.is_list_item(line):

                print("line: ", line)
                item = self.parse_list_item(line)
                print("parsed list-item: ", item)
                
                level = self.get_current_level(parents)
                
                if not in_list:
                    in_list = True
                    
                    parents[level+1] = doc.add_group(
                        parent=parents[level], name="list", label=GroupLabel.LIST
                    )
                    indents[level+1] = item["indent"]
                    
                elif in_list and item["indent"]>indents[level]:
                    parents[level+1] = doc.add_group(
                        parent=parents[level], name="list", label=GroupLabel.LIST
                    )
                    indents[level+1] = item["indent"]

                elif in_list and item["indent"]<indents[level]:                    

                    print(item["indent"], " => ", indents[level])
                    while item["indent"]<indents[level]:
                        print(item["indent"], " => ", indents[level])
                        parents[level] = None
                        indents[level] = None
                        level -= 1
                        
                doc.add_list_item(item["text"], parent=self.get_current_parent(parents))

            elif in_list and not self.is_list_item(line):
                in_list = False

                level = self.get_current_level(parents)
                parents[level]=None

            # Tables
            elif line.strip()=="|===" and not in_table: # start of table
                in_table = True
                
            elif self.is_table_line(line): # within a table
                in_table = True
                table_data.append(self.parse_table_line(line))

            elif in_table and ((not self.is_table_line(line)) or line.strip()=="|==="): # end of table

                caption = None
                if len(caption_data)>0:
                    caption = doc.add_text(text=" ".join(caption_data), label=DocItemLabel.CAPTION)

                caption_data = []                
                
                data = self.populate_table_as_grid(table_data)
                doc.add_table(data=data, parent=self.get_current_parent(parents), caption=caption)

                in_table = False
                table_data = []
                
            # Picture
            elif self.is_picture(line):

                caption = None
                if len(caption_data)>0:
                    caption = doc.add_text(text=" ".join(caption_data), label=DocItemLabel.CAPTION)

                caption_data = []                
                
                item = self.parse_picture(line)
                print(item)

                image = ImageRef(mimetype="image/png", size=[100,100], dpi=70, uri=item["uri"])
                doc.add_picture(image=image, caption=caption)
                
            # Caption
            elif self.is_caption(line) and len(caption_data)==0:
                item = self.parse_caption(line)
                caption_data.append(item["text"])

            elif len(line.strip())>0 and len(caption_data)>0: # allow multiline captions
                item = self.parse_text(line)
                caption_data.append(item["text"])
                
            # Plain text
            elif len(line.strip())==0 and len(text_data)>0:
                doc.add_text(text=" ".join(text_data), label=DocItemLabel.PARAGRAPH,
                             parent=self.get_current_parent(parents))
                text_data = []
                
            elif len(line.strip())>0: # allow multiline texts
                
                item = self.parse_text(line)
                text_data.append(item["text"])

        if len(text_data) > 0:
            doc.add_text(text=" ".join(text_data), label=DocItemLabel.PARAGRAPH,
                         parent=self.get_current_parent(parents))
            text_data = []
                
        if in_table and len(table_data) > 0:
            data = self.populate_table_as_grid(table_data)
            doc.add_table(data=data, parent=self.get_current_parent(parents))

            in_table = False
            table_data = []

        return doc

    def get_current_level(self, parents):
        for k,v in parents.items():
            if v==None and k>0:
                return k-1

        return 0
    
    def get_current_parent(self, parents):
        for k,v in parents.items():
            if v==None and k>0:
                return parents[k-1]

        return None
            
    #   =========   Title
    def is_title(self, line):
        return re.match(r"^= ", line)

    def parse_title(self, line):
        return {"type": "title", "text": line[2:].strip(), "level":0}

    #   =========   Section headers
    def is_section_header(self, line):
        return re.match(r"^==+", line)

    def parse_section_header(self, line):
        match = re.match(r"^(=+)\s+(.*)", line)
        
        marker = match.group(1)  # The list marker (e.g., "*", "-", "1.")
        text = match.group(2)    # The actual text of the list item
        
        header_level = marker.count("=")  # number of '=' represents level
        return {
            "type": "header",
            "level": header_level-1,
            "text": text.strip(),
        }

    #   =========   Lists
    def is_list_item(self, line):
        return re.match(r"^(\s)*(\*|-|\d+\.|\w+\.) ", line)

    def parse_list_item(self, line):
        """Extract the item marker (number or bullet symbol) and the text of the item."""

        match = re.match(r"^(\s*)(\*|-|\d+\.)\s+(.*)", line)
        if match:
            indent = match.group(1)
            marker = match.group(2)  # The list marker (e.g., "*", "-", "1.")
            text = match.group(3)   # The actual text of the list item
            
            if marker=="*" or marker=="-":
                return {"type": "list_item", "marker": marker, "text": text.strip(),
                        "numbered": False, "indent": 0 if indent==None else len(indent)}
            else:
                return {"type": "list_item", "marker": marker, "text": text.strip(),
                        "numbered": True, "indent": 0 if indent==None else len(indent)}
        else:
            # Fallback if no match
            return {"type": "list_item", "marker": item_marker, "text": line,
                    "numbered": False, "indent": 0}
    
    #   =========   Tables
    def is_table_line(self, line):
        return re.match(r"^\|.*\|", line)

    def parse_table_line(self, line):
        # Split table cells and trim extra spaces
        return [cell.strip() for cell in line.split("|") if cell.strip()]

    def populate_table_as_grid(self, table_data):

        num_rows = len(table_data)

        # Adjust the table data into a grid format
        num_cols = max(len(row) for row in table_data)

        data = TableData(num_rows=num_rows, num_cols=num_cols, table_cells=[])
        for row_idx, row in enumerate(table_data):
            # Pad rows with empty strings to match column count
            # grid.append(row + [''] * (max_cols - len(row)))

            for col_idx, text in enumerate(row):
                row_span = 1
                col_span = 1

                cell = TableCell(
                    text=text,
                    row_span=row_span,
                    col_span=col_span,
                    start_row_offset_idx=row_idx,
                    end_row_offset_idx=row_idx + row_span,
                    start_col_offset_idx=col_idx,
                    end_col_offset_idx=col_idx + col_span,
                    col_header=False,
                    row_header=False,
                )
                data.table_cells.append(cell)

        return data

    #   =========   Pictures
    def is_picture(self, line):
        return re.match(r"^image::", line)

    def parse_picture(self, line):
        """
        Parse an image macro, extracting its path and attributes.
        Syntax: image::path/to/image.png[Alt Text, width=200, height=150, align=center]
        """
        mtch = re.match(r"^image::(.+)\[(.*)\]$", line)
        if mtch:
            picture_path = mtch.group(1).strip()
            attributes = mtch.group(2).split(',')
            picture_info = {"type": "picture", "uri": picture_path}

            # Extract optional attributes (alt text, width, height, alignment)
            if attributes:
                picture_info["alt"] = attributes[0].strip() if attributes[0] else ""
                for attr in attributes[1:]:
                    key, value = attr.split('=')
                    picture_info[key.strip()] = value.strip()
                    
            return picture_info
        
        return {"type": "picture", "uri": line}

    #   =========   Captions
    def is_caption(self, line):
        return re.match(r"^\.(.+)", line)

    def parse_caption(self, line):
        mtch = re.match(r"^\.(.+)", line)
        if mtch:
            text = mtch.group(1)
            return {"type": "caption", "text": text}

        return {"type": "caption", "text": ""}
        
    #   =========   Plain text
    def parse_text(self, line):
        return {"type": "text", "text": line.strip()}
