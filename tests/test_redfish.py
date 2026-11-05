import unittest
from overwatch.redfish import RedfishClient, RedfishError
from overwatch.simulator import LabServer


class RedfishTests(unittest.TestCase):
    def setUp(self):
        self.lab = LabServer(port=0)
        self.lab.start()
        self.addCleanup(self.lab.close)
        self.client = RedfishClient(self.lab.url, environment="simulation")

    def test_discovers_links_and_reads_sensor_with_stable_asset_id(self):
        readings = self.client.collect()
        self.assertEqual(readings[0]["asset_id"], "LAB-SERVER-001")
        self.assertEqual(readings[0]["temperature"], 45)
        self.assertTrue(readings[0]["observed_at"])

    def test_degraded_fixture_is_observable(self):
        self.lab.set_scenario("degraded")
        self.assertEqual(self.client.collect()[0]["temperature"], 91)

    def test_dark_node_raises_explicit_collection_error(self):
        self.lab.set_scenario("dark")
        with self.assertRaises(RedfishError):
            self.client.collect()

    def test_remote_link_cannot_redirect_collector_credentials(self):
        with self.assertRaises(RedfishError):
            self.client.get("https://attacker.invalid/redfish/v1/")

    def test_production_requires_tls(self):
        with self.assertRaises(RedfishError):
            RedfishClient(self.lab.url, environment="production")

    def test_generic_success_does_not_change_health(self):
        self.lab.set_scenario("degraded")
        self.client.request("POST", "/redfish/v1/Systems/node-a/Actions/Oem.OverwatchRepair",
                            {"operation_id": "test-op", "recover": False})
        self.assertEqual(self.client.collect()[0]["temperature"], 91)
