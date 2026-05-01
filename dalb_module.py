"""
DALB Module - Dynamic and Adaptive Load Balancing logic.
Based on: "A Load Balancing Strategy for SDN Controller based on Distributed Decision"
Beihang University, IEEE TrustCom 2014.
"""

import statistics


class DALBMetrics:

    # Paper uses ρ threshold of 0.7
    RHO_THRESHOLD = 0.7
    # Initial Controller Threshold (CT) = 1000 packet-in/s (Floodlight default)
    ICT = 1000

    def calculate_switch_load(self, N, F, R, w1=0.1, w2=0.8, w3=0.1):
        """
        Compute switch load contribution to its controller. Formula (1) in paper.

        C_Load = w1*N + w2*F + w3*R

        Args:
            N  (int):   Number of flow table entries on the switch.
            F  (float): Average packet-in arrival rate (packets/s).
            R  (float): Round-trip time from switch to controller (ms).
            w1 (float): Weight for N (default 0.1 — flow entries change slowly).
            w2 (float): Weight for F (default 0.8 — dominant metric per paper).
            w3 (float): Weight for R (default 0.1 — small in LAN environments).

        Returns:
            float: Weighted load value for this switch.
        """
        assert abs(w1 + w2 + w3 - 1.0) < 1e-9, "Weights must sum to 1.0"
        return w1 * N + w2 * F + w3 * R

    def calculate_controller_load(self, switch_loads):
        """
        Aggregate load of a controller as the sum of all its switches' loads.

        Args:
            switch_loads (list[float]): Load values for each switch managed
                                        by the controller.

        Returns:
            float: Total controller load.
        """
        return sum(switch_loads)

    def calculate_rho(self, load_list):
        """
        Compute system-wide load balancing rate ρ. Formula (2) in paper.

        ρ = mean(loads) / max(loads)

        ρ close to 1.0 → cluster is well-balanced.
        ρ < RHO_THRESHOLD (0.7) → migration should be considered.

        Args:
            load_list (list[float]): Load of every controller in the cluster.

        Returns:
            float: ρ value in [0, 1]. Returns 1.0 when list is empty or max=0
                   (trivially balanced, nothing to migrate).
        """
        if not load_list:
            return 1.0
        max_load = max(load_list)
        if max_load == 0:
            return 1.0
        mean_load = statistics.mean(load_list)
        return mean_load / max_load

    def adaptive_ct(self, load_list, ict=1000):
        """
        Algorithm 1 (AdaptiveCT) from the paper: dynamically adjust the load
        collection threshold to avoid excessive inter-controller messaging when
        the whole cluster is overloaded.

        δ = mean(load_list)
        if δ > ICT:
            CT = δ     # raise threshold to average load
        else:
            CT = ICT   # keep initial threshold

        Args:
            load_list (list[float]): Current load of all controllers in cluster.
            ict       (float):       Initial CT value (default 1000).

        Returns:
            float: New CT value.
        """
        if not load_list:
            return float(ict)
        delta = statistics.mean(load_list)
        if delta > ict:
            return delta
        return float(ict)

    def select_switch_to_migrate(self, switches_info, L_overloaded, L_target):
        """
        Choose which switch to migrate from the overloaded controller to the
        target controller. Formula (3) in paper:

            L_Migrate ≤ (L_overloaded - L_target) / 2

        We prefer the switch with the largest load that still satisfies the
        constraint, so that the overloaded controller sheds as much load as
        possible without the target controller becoming the new bottleneck.

        After migration the overloaded controller's load should still be
        below CT. The condition ensures:
            L_overloaded - L_Migrate >= L_target

        Args:
            switches_info (list[dict]): Each dict has keys:
                                        'dpid'  (int)   — datapath id
                                        'name'  (str)   — human name, e.g. 'S3'
                                        'load'  (float) — switch C_Load value
            L_overloaded  (float): Total load of the overloaded controller.
            L_target      (float): Total load of the target (under-loaded) ctrl.

        Returns:
            dict | None: The selected switch dict, or None if no switch
                         satisfies the constraint.
        """
        max_allowed = (L_overloaded - L_target) / 2.0
        candidates = [s for s in switches_info if s['load'] <= max_allowed]
        if not candidates:
            return None
        # Pick the candidate that sheds the most load (largest load ≤ max_allowed)
        return max(candidates, key=lambda s: s['load'])

    def should_migrate(self, my_load, all_loads, ct):
        """
        Evaluate the two migration conditions from the paper's Decision Maker:

        Condition 1: ρ < RHO_THRESHOLD (0.7)
                     → cluster is imbalanced, migration is globally beneficial.
        Condition 2: my_load == max(all_loads.values())
                     → only the most-loaded controller initiates migration to
                        prevent two controllers migrating to each other
                        simultaneously.

        Args:
            my_load   (float):       This controller's total load.
            all_loads (dict[str, float]): Load of every controller, including
                                          self. E.g. {'A': 1820.0, 'B': 200.0}
            ct        (float):       Current CT (unused in decision, kept for
                                     caller convenience / future extension).

        Returns:
            tuple[bool, str]: (should_migrate, human-readable reason string).
        """
        load_values = list(all_loads.values())
        rho = self.calculate_rho(load_values)

        cond1 = rho < self.RHO_THRESHOLD
        cond2 = my_load >= max(load_values)  # >= handles float equality edge case

        if cond1 and cond2:
            reason = (
                f"ρ={rho:.3f} < {self.RHO_THRESHOLD} [C1 ✅] "
                f"AND my_load={my_load} is MAX [C2 ✅] → MIGRATE"
            )
            return True, reason

        reasons = []
        if not cond1:
            reasons.append(f"ρ={rho:.3f} >= {self.RHO_THRESHOLD} [C1 ✗ — cluster balanced]")
        if not cond2:
            reasons.append(f"my_load={my_load} is NOT max of {load_values} [C2 ✗ — not the busiest]")
        return False, " | ".join(reasons)


# ---------------------------------------------------------------------------
# Unit tests — numbers match paper examples and the project scenario
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    m = DALBMetrics()

    print("=" * 60)
    print("DALB Module Unit Tests")
    print("=" * 60)

    # --- calculate_switch_load ---
    load = m.calculate_switch_load(N=12, F=120, R=1)
    print(f"\n[1] switch_load(N=12, F=120, R=1) = {load:.2f}  (expect 97.3)")
    assert abs(load - 97.3) < 0.1, f"Got {load}"

    # --- calculate_controller_load ---
    ctrl_load = m.calculate_controller_load([97.3, 65.0])
    print(f"[2] controller_load([97.3, 65.0]) = {ctrl_load:.1f}  (expect 162.3)")

    # --- calculate_rho  (paper Fig 6 scenario: A=580, B=1820) ---
    loads = [580.0, 1820.0]
    rho = m.calculate_rho(loads)
    print(f"\n[3] rho([580, 1820]) = {rho:.3f}  (expect 0.659 — below 0.7 → migrate)")
    assert rho < 0.7

    rho_bal = m.calculate_rho([1000.0, 1000.0])
    print(f"[4] rho([1000, 1000]) = {rho_bal:.3f}  (expect 1.000 — balanced)")
    assert rho_bal == 1.0

    rho_empty = m.calculate_rho([])
    print(f"[5] rho([]) = {rho_empty:.3f}  (expect 1.000 — trivially balanced)")
    assert rho_empty == 1.0

    # --- adaptive_ct ---
    new_ct = m.adaptive_ct([580.0, 1820.0], ict=1000)
    print(f"\n[6] adaptive_ct([580, 1820], ict=1000): delta=1200 > 1000 → CT={new_ct:.1f}")
    # Paper Fig6 result: AdaptiveCT([1820, 200]) = 1010 → CT stays 1000 because
    # delta = (1820+200)/2 = 1010 > 1000 → CT = 1010
    new_ct2 = m.adaptive_ct([1820.0, 200.0], ict=1000)
    print(f"[7] adaptive_ct([1820, 200], ict=1000): delta=1010 > 1000 → CT={new_ct2:.1f}  (expect 1010)")

    # Below ICT case
    new_ct3 = m.adaptive_ct([200.0, 400.0], ict=1000)
    print(f"[8] adaptive_ct([200, 400], ict=1000): delta=300 < 1000 → CT={new_ct3:.1f}  (expect 1000)")
    assert new_ct3 == 1000.0

    # --- select_switch_to_migrate ---
    # B has load 1820, A has 200 → max_allowed = (1820-200)/2 = 810
    switches = [
        {'dpid': 3, 'name': 'S3', 'load': 420.0},
        {'dpid': 4, 'name': 'S4', 'load': 380.0},
        {'dpid': 5, 'name': 'S5', 'load': 1020.0},  # exceeds 810 → excluded
    ]
    chosen = m.select_switch_to_migrate(switches, L_overloaded=1820.0, L_target=200.0)
    print(f"\n[9] select_switch(overloaded=1820, target=200): chose {chosen['name']} "
          f"load={chosen['load']}  (expect S3 or S4, largest ≤ 810)")
    assert chosen['name'] == 'S3'  # 420 > 380 and both ≤ 810

    none_case = m.select_switch_to_migrate(switches, L_overloaded=250.0, L_target=200.0)
    print(f"[10] select_switch(overloaded=250, target=200): {none_case}  (expect None)")
    assert none_case is None

    # --- should_migrate ---
    all_loads = {'A': 200.0, 'B': 1820.0}
    result, reason = m.should_migrate(my_load=1820.0, all_loads=all_loads, ct=1000)
    print(f"\n[11] should_migrate(B=1820, all={{A:200,B:1820}}): {result}  — {reason}")
    assert result is True

    result2, reason2 = m.should_migrate(my_load=200.0, all_loads=all_loads, ct=1000)
    print(f"[12] should_migrate(A=200, all={{A:200,B:1820}}): {result2}  — {reason2}")
    assert result2 is False

    # Balanced scenario — ρ ≥ 0.7
    bal_loads = {'A': 900.0, 'B': 1100.0}
    result3, reason3 = m.should_migrate(my_load=1100.0, all_loads=bal_loads, ct=1000)
    print(f"[13] should_migrate(B=1100, balanced): {result3}  — {reason3}")
    assert result3 is False

    print("\n" + "=" * 60)
    print("All tests PASSED ✅")
    print("=" * 60)
