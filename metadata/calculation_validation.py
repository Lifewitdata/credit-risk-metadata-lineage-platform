"""Reconcile a documented ratio without overwriting disputed source values."""
from pathlib import Path
import numpy as np
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]

def validate_ratio(frame,tolerance=1e-6):
    out=frame[['loan_id','loan_amount_gbp','annual_revenue_gbp','dti_ratio']].copy()
    valid=np.isfinite(out.annual_revenue_gbp)&(out.annual_revenue_gbp>0)&np.isfinite(out.loan_amount_gbp)
    out['expected']=np.where(valid,out.loan_amount_gbp/out.annual_revenue_gbp,np.nan)
    out['difference']=out.dti_ratio-out.expected
    out['tolerance']=tolerance
    out['status']=np.where(~valid,'blocked_input',np.where(np.isfinite(out.dti_ratio)&(out.difference.abs()<=tolerance),'pass','mismatch'))
    return out

def run():
    result=validate_ratio(pd.read_csv(ROOT/'data/raw/credit_datamart_sme_loans.csv'))
    target=ROOT/'reports';target.mkdir(exist_ok=True)
    result.to_csv(target/'calculation_validation.csv',index=False,encoding='utf-8',lineterminator='\n')
    return result

if __name__=='__main__':
    print(run().to_string(index=False))
