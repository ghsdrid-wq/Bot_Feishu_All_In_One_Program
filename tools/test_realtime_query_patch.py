"""Check DataMashup package integrity and permission binding preservation."""
import base64
import hashlib
import io
import unittest
import xml.etree.ElementTree as ET
import zipfile

import win32crypt

from tools.update_realtime_query import replacement_xml


class MashupPatchTests(unittest.TestCase):
    def test_only_target_query_changes_and_binding_matches_new_package(self):
        stream = io.BytesIO()
        original_m = 'section Section1;\nshared Other = 123;\nshared REALTIME_DB = let Source = File.Contents("D:\\raw\\old.xlsx") in Source;\n'
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("Formulas/Section1.m", original_m)
            archive.writestr("Config/Package.xml", b"unchanged-config")
        package = stream.getvalue()
        permissions = b"unchanged-permissions"
        metadata = b"unchanged-metadata"
        field = lambda value: len(value).to_bytes(4, "little") + value
        blob = bytes(4) + field(package) + field(permissions) + field(metadata) + field(b"old-binding")
        xml = ('<?xml version="1.0" encoding="utf-16"?><DataMashup xmlns="http://schemas.microsoft.com/DataMashup">'
               + base64.b64encode(blob).decode() + '</DataMashup>').encode("utf-16")
        formula = 'let Source = File.Contents("C:\\0DWS\\RealtimeDB.xlsx"), Result = "最新操作类型" in Result'
        edited = base64.b64decode(ET.fromstring(replacement_xml(xml, formula)).text)
        fields = []
        offset = 4
        for _ in range(4):
            length = int.from_bytes(edited[offset:offset+4], "little")
            fields.append(edited[offset+4:offset+4+length])
            offset += 4 + length
        self.assertEqual(offset, len(edited))
        self.assertEqual(fields[1:3], [permissions, metadata])
        with zipfile.ZipFile(io.BytesIO(fields[0])) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read("Config/Package.xml"), b"unchanged-config")
            text = archive.read("Formulas/Section1.m").decode("utf-8")
            self.assertIn('shared Other = 123;', text)
            self.assertIn('D:\\raw\\old.xlsx', text)
            self.assertIn('最新操作类型', text)
            self.assertNotIn('C:\\0DWS', text)
        checksum = win32crypt.CryptUnprotectData(fields[3], b"DataExplorer Package Components", None, None, 0)[1]
        self.assertEqual(checksum, b"".join(field(hashlib.sha256(value).digest()) for value in fields[:2]))


if __name__ == "__main__":
    unittest.main()
