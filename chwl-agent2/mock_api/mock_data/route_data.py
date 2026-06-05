ROUTES = {
    # ── 家庭场景路线 ──
    ("home_wangjing", "act_family_001", "taxi"): {
        "from": "home_wangjing", "to": "act_family_001", "mode": "taxi",
        "distance_km": 2.8, "duration_min": 13, "walk_distance_m": 80, "traffic_level": "normal", "source": "mock_route",
    },
    ("act_family_001", "rest_family_001", "taxi"): {
        "from": "act_family_001", "to": "rest_family_001", "mode": "taxi",
        "distance_km": 0.9, "duration_min": 8, "walk_distance_m": 120, "traffic_level": "normal", "source": "mock_route",
    },
    ("act_family_001", "rest_family_002", "taxi"): {
        "from": "act_family_001", "to": "rest_family_002", "mode": "taxi",
        "distance_km": 1.2, "duration_min": 10, "walk_distance_m": 150, "traffic_level": "normal", "source": "mock_route",
    },
    ("act_family_001", "rest_family_003", "taxi"): {
        "from": "act_family_001", "to": "rest_family_003", "mode": "taxi",
        "distance_km": 0.8, "duration_min": 6, "walk_distance_m": 200, "traffic_level": "normal", "source": "mock_route",
    },
    ("rest_family_002", "walk_001", "walk"): {
        "from": "rest_family_002", "to": "walk_001", "mode": "walk",
        "distance_km": 0.3, "duration_min": 5, "walk_distance_m": 300, "traffic_level": "normal", "source": "mock_route",
    },

    # ── 朋友场景路线 ──
    ("home_wangjing", "act_friend_004", "taxi"): {
        "from": "home_wangjing", "to": "act_friend_004", "mode": "taxi",
        "distance_km": 2.4, "duration_min": 12, "walk_distance_m": 60, "traffic_level": "normal", "source": "mock_route",
    },
    ("home_wangjing", "act_friend_003", "taxi"): {
        "from": "home_wangjing", "to": "act_friend_003", "mode": "taxi",
        "distance_km": 10.6, "duration_min": 32, "walk_distance_m": 200, "traffic_level": "normal", "source": "mock_route",
    },
    ("act_friend_004", "rest_friend_001", "taxi"): {
        "from": "act_friend_004", "to": "rest_friend_001", "mode": "taxi",
        "distance_km": 1.5, "duration_min": 10, "walk_distance_m": 100, "traffic_level": "normal", "source": "mock_route",
    },
    ("act_friend_004", "rest_friend_003", "taxi"): {
        "from": "act_friend_004", "to": "rest_friend_003", "mode": "taxi",
        "distance_km": 1.8, "duration_min": 12, "walk_distance_m": 150, "traffic_level": "normal", "source": "mock_route",
    },
    ("act_friend_003", "rest_friend_001", "taxi"): {
        "from": "act_friend_003", "to": "rest_friend_001", "mode": "taxi",
        "distance_km": 0.8, "duration_min": 6, "walk_distance_m": 50, "traffic_level": "normal", "source": "mock_route",
    },
    ("rest_friend_001", "walk_003", "walk"): {
        "from": "rest_friend_001", "to": "walk_003", "mode": "walk",
        "distance_km": 0.5, "duration_min": 6, "walk_distance_m": 500, "traffic_level": "normal", "source": "mock_route",
    },
}
