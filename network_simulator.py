import random
import time

class NetworkSimulator:
    """
    Simulates actual 5G Network Slice conditions via programmatic delays
    and packet drops, avoiding OS-level dependencies like Windows traffic control.
    """
    PROFILES = {
        "URLLC": {"latency_ms": (1, 5), "drop_prob": 0.001},
        "eMBB_Excellent": {"latency_ms": (15, 30), "drop_prob": 0.01},
        "eMBB_Congested": {"latency_ms": (100, 300), "drop_prob": 0.15},
        "Edge_Failure": {"latency_ms": (500, 2000), "drop_prob": 0.4}
    }

    def __init__(self, profile_name="eMBB_Excellent"):
        if profile_name not in self.PROFILES:
            profile_name = "eMBB_Excellent"
        self.profile_name = profile_name
        self.profile = self.PROFILES[profile_name]
        self.current_latency_ms = 0

    def set_profile(self, profile_name):
        if profile_name in self.PROFILES:
            self.profile_name = profile_name
            self.profile = self.PROFILES[profile_name]

    def simulate_delay(self):
        """
        Sleeps the thread to simulate 5G network latency.
        Returns the applied latency in milliseconds.
        """
        latency = random.randint(self.profile["latency_ms"][0], self.profile["latency_ms"][1])
        self.current_latency_ms = latency
        time.sleep(latency / 1000.0)
        return latency

    def should_drop_packet(self):
        """
        Simulates network packet loss by returning True based on drop_prob.
        """
        return random.random() < self.profile["drop_prob"]

# Global singleton
net_sim = NetworkSimulator("eMBB_Excellent")
