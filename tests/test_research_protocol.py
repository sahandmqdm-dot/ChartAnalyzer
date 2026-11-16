import numpy as np
import pytest
from research_protocol import chronological_split, purged_walk_forward_splits, audit_feature_availability


def test_chronological_split_is_non_overlapping_and_ordered():
    x=chronological_split(1000)
    assert x['train']==(0,600) and x['validation']==(600,800) and x['holdout']==(800,1000)


def test_chronological_split_rejects_bad_ratios():
    with pytest.raises(ValueError): chronological_split(100,train_ratio=.7,validation_ratio=.2,holdout_ratio=.2)


def test_purged_cv_removes_labels_overlapping_test_start():
    ends=np.arange(300)+2
    folds=purged_walk_forward_splits(300,n_splits=3,min_train=60,test_size=40,label_end_indices=ends,embargo_bars=5)
    for f in folds:
        assert np.all(ends[f['train_indices']] < f['test_start'])
        assert f['test_indices'][0] == f['test_start']


def test_feature_availability_audit_detects_future_information():
    out=audit_feature_availability([1,2,4],[1,3,3])
    assert out['status']=='BLOCKED' and out['violations']==[2]
