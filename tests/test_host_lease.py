import time
import pytest
from rubrika.errors import ResearchError
from rubrika.storage import Store

def test_one_request_in_flight_per_host_across_stores(cfg):
    a,b=Store(cfg),Store(cfg)
    try:
        assert a.claim_host('h.example','A',interval=0.2,lease=5,groups=['g'])==0
        assert b.claim_host('h.example','B',interval=0.2,lease=5,groups=['g'])>0      # A still in flight
        assert b.claim_host('other.example','B',interval=0.2,lease=5,groups=['g'])==0  # other host independent
        time.sleep(0.3)
        assert b.claim_host('h.example','B',interval=0.2,lease=5,groups=['g'])>0      # interval met, lease not
        a.release_host('h.example','B')                                                # wrong token: no effect
        assert b.claim_host('h.example','B',interval=0.2,lease=5,groups=['g'])>0
        a.release_host('h.example','A')
        assert b.claim_host('h.example','B',interval=0.2,lease=5,groups=['g'])==0
    finally:
        a.close(); b.close()

def test_spacing_is_measured_from_actual_start(cfg):
    a,b=Store(cfg),Store(cfg)
    try:
        assert a.claim_host('h.example','A',interval=0.5,lease=5,groups=['g'])==0
        a.release_host('h.example','A')
        wait=b.claim_host('h.example','B',interval=0.5,lease=5,groups=['g'])
        assert 0.3<wait<=0.5                     # B may not start before A's start + interval
    finally:
        a.close(); b.close()

def test_cooldown_from_another_process_stops_a_waiting_request(cfg):
    a,b=Store(cfg),Store(cfg)
    try:
        a.meta_set('source_block_until:g',time.time()+60)
        with pytest.raises(ResearchError) as e: b.claim_host('h.example','B',interval=0,lease=5,groups=['g'])
        assert e.value.code=='RATE_LIMITED'
    finally:
        a.close(); b.close()

def test_legacy_global_cooldown_stops_claim(cfg):
    a=Store(cfg)
    try:
        a.meta_set('source_block_until',time.time()+60)
        with pytest.raises(ResearchError) as e: a.claim_host('h.example','A',interval=0,lease=5,groups=['g'])
        assert e.value.code=='RATE_LIMITED'
    finally:
        a.close()

def test_foreign_lease_wait_is_capped_at_one_second(cfg):
    a,b=Store(cfg),Store(cfg)
    try:
        assert a.claim_host('h.example','A',interval=0,lease=30,groups=['g'])==0
        assert 0<b.claim_host('h.example','B',interval=0,lease=5,groups=['g'])<=1.0
    finally:
        a.close(); b.close()

def test_expired_lease_does_not_block_forever(cfg):
    a=Store(cfg)
    try:
        assert a.claim_host('h.example','A',interval=0,lease=0.1,groups=['g'])==0      # crashed holder
        time.sleep(0.15)
        assert a.claim_host('h.example','B',interval=0,lease=5,groups=['g'])==0
    finally:
        a.close()


def test_retry_after_is_at_least_one_second(cfg):
    import time
    a=Store(cfg)
    try:
        a.meta_set('source_block_until:g',time.time()+0.2)
        with pytest.raises(ResearchError) as e: a.claim_host('h.example','A',interval=0,lease=5,groups=['g'])
        assert e.value.details['retry_after']==1
    finally: a.close()
