"""Exclusive wall-clock phases; no guessed percentage attribution."""
import time

class ExportTimings:
    def __init__(self):
        self.started=self.last=time.perf_counter()
        self.phases={}
        self.details={}

    def mark(self,name,*,group=None):
        now=time.perf_counter()
        elapsed=now-self.last
        self.last=now
        phase=group or name
        self.phases[phase]=self.phases.get(phase,0.0)+elapsed
        if group:
            details=self.details.setdefault(group,{})
            details[name]=details.get(name,0.0)+elapsed

    def snapshot(self):
        return {'clock':'perf_counter','phases_seconds':dict(self.phases),
                'phase_details_seconds':{name:dict(parts) for name,parts in self.details.items()},
                'total_to_report_seconds':time.perf_counter()-self.started,
                'scope':'worker start to report snapshot; excludes report serialization and temporary-file cleanup'}
