import importlib.util
from pathlib import Path

path=Path(__file__).parents[1]/'indicators/research/fit_indicator_rules.py'
spec=importlib.util.spec_from_file_location('fit_indicator_rules',path)
fit=importlib.util.module_from_spec(spec)
spec.loader.exec_module(fit)


def test_shifted_channel_confirmation_and_reset():
    bars=[{'c':c} for c in [12,12,12,12,9,7,12]]
    f={'high100':[10]*7,'low100':[8]*7}
    spec={'family':'channel_confirm','length':100,'shift':1,'confirm':4,'inside':'bear'}
    assert fit.predict(spec,bars,f)==[1,1,1,2,2,2,0]


def test_rule_predictions_do_not_read_original_state_or_markers():
    bars=[{'c':c,'state':99,'buy':1,'sell':1} for c in [7,12,12,12,12,7]]
    f={'high100':[10]*6,'low100':[8]*6}
    spec={'family':'channel_confirm','length':100,'shift':1,'confirm':4,'inside':'bear'}
    first=fit.predict(spec,bars,f)
    for b in bars:b.update(state=-999,buy=None,sell=None)
    assert fit.predict(spec,bars,f)==first


def test_signal_metrics_count_false_and_missed_markers():
    b=[{'t':i*10,'period':10,'buy':int(i==2),'sell':None} for i in range(4)]
    result=fit.signal_score([0,2,2,0],b,[1,2,3])
    assert result['BUY']['extra_signals']==1
    assert result['BUY']['missed_signals']==1
    assert result['BUY']['exact_bar_matches']==0


def test_ema_recurrence():
    assert fit.ema([6,12,18],11)==[6,7,7+11/6]
