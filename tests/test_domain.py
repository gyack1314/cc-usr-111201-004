"""核对领域原语的业务数值。"""
import unittest
from analysis_data.io import load
from analysis_data.domain import summarize
class DomainTests(unittest.TestCase):
    def test_parallel_material(self):
        row=summarize(load('data/normal.json')[0])[0]
        self.assertEqual(row['sum_node_duration'],22);self.assertEqual(row['observed_wall'],12)
        self.assertEqual(row['nodes'],5);self.assertEqual(row['edges'],5)
