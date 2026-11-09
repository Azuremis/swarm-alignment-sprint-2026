"""One reviewed channel-factorial extension; prior recovery remains immutable."""
from copy import deepcopy
from recovery_reservations import RecoveryBudgetBook, digest
from attempts import money


class ChannelBudgetBook(RecoveryBudgetBook):
    def __init__(self, path, policy):
        if type(policy.get("total_attempt_limit")) is not int or policy.get("version") != "bounded-channel-factorial-v1" or policy.get("total_attempt_limit") != 288:
            raise ValueError("exact channel phase scope required")
        if "parent_policy" not in policy:
            raise ValueError("original recovery authorization required")
        parent = deepcopy(policy["parent_policy"])
        self.parent = RecoveryBudgetBook(path, parent)
        base = {k: v for k, v in policy.items() if k != "parent_policy"}
        base.update(version="bounded-ring-recovery-v1", total_attempt_limit=96)
        super().__init__(path, base)
        if (policy["baseline_rows"] != parent["baseline_rows"] + 95
                or policy["original_run_id"] != parent["original_run_id"]
                or policy["uncertain_attempt_id"] != parent["uncertain_attempt_id"]
                or policy["recovery_run_id"] == parent["recovery_run_id"]):
            raise ValueError("completed prior 96-attempt phase required")
        self._policy = deepcopy(policy)
        self._policy_digest = digest(policy)

    def _authorization_key(self):
        return "channel-extension:" + self._policy["uncertain_attempt_id"]

    def _baseline(self, rows):
        original = super()._baseline(rows)
        self.parent._baseline(rows)
        prior = rows[self.parent._policy["baseline_rows"]:self._policy["baseline_rows"]]
        if len(prior) != 95:
            raise ValueError("prior recovery count changed")
        for _, state, bound, row in prior:
            self.parent._identity(row)
            if (state != "settled" or row["status"] != "success" or row["cost_usd"] is None
                    or bound != money(self._policy["maximum_request_usd"])
                    or money(row["cost_usd"]) > bound):
                raise ValueError("prior recovery not fully settled")
        return original

    def _prior_records(self, rows):
        return [self._baseline(rows)] + rows[self.parent._policy["baseline_rows"]:self._policy["baseline_rows"]]

    def _binding(self, db):
        self.parent._binding(db)
        super()._binding(db)
