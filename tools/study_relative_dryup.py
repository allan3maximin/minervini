"""相対枯れ度(市場出来高で正規化したdry-up)の予測力検証スクリプト。

2026-07-28 単発の研究用。data/prices/*.parquet(約2年・1002銘柄)から
ベース近傍セットアップを抽出し、絶対枯れ/相対枯れ/出来高ベータ残差zの
3指標をブレイク成否に対して比較する。結論は log.md 2026-07-28(133) 参照。

実行: python3 tools/study_relative_dryup.py  (段階ごとに/tmpへpickle)
"""
# --- step A: 価格ロード ---
import glob, pickle, numpy as np, pandas as pd
files = sorted(glob.glob('data/prices/*.parquet'))
store = {}
for f in files:
    code = f.split('/')[-1][:-8]
    d = pd.read_parquet(f)
    if len(d) < 300: continue
    store[code] = dict(
        date=np.array([str(x)[:10] for x in d['date']]),
        high=d['high'].to_numpy('f8'), low=d['low'].to_numpy('f8'),
        close=d['close'].to_numpy('f8'), vol=d['volume'].to_numpy('f8'))
pickle.dump(store, open('/tmp/px.pkl','wb'))
print(len(store), 'stocks')

# --- step B: 特徴量 + 市場出来高レジーム ---
import pickle, numpy as np
S = pickle.load(open('/tmp/px.pkl','rb'))
alldates = sorted({d for v in S.values() for d in v['date']})
di = {d:i for i,d in enumerate(alldates)}; T=len(alldates)
def rmean(a,k):
    c=np.concatenate(([0.0],np.cumsum(a))); out=np.full(len(a),np.nan)
    out[k-1:]=(c[k:]-c[:-k])/k; return out
def rmax(a,k):
    out=np.full(len(a),np.nan)
    for i in range(k-1,len(a)): out[i]=a[i-k+1:i+1].max()
    return out
def rmin(a,k):
    out=np.full(len(a),np.nan)
    for i in range(k-1,len(a)): out[i]=a[i-k+1:i+1].min()
    return out
def rmed(a,k):
    out=np.full(len(a),np.nan)
    if len(a)>=k:
        w=np.lib.stride_tricks.sliding_window_view(a,k)
        out[k-1:]=np.median(w,axis=1)
    return out

F={}
rv_mat=np.full((len(S),T),np.nan); codes=list(S)
for ci,code in enumerate(codes):
    v=S[code]; c=v['close']; vol=v['vol']
    vma50=rmean(vol,50)
    with np.errstate(all='ignore'): rv=vol/vma50
    f=dict(v)
    f['vma50']=vma50; f['rv']=rv
    f['ma50']=rmean(c,50); f['ma200']=rmean(c,200)
    f['h250']=rmax(v['high'],250); f['l250']=rmin(v['low'],250)
    f['h20']=rmax(v['high'],20)
    f['volmed10']=rmed(vol,10)
    f['idx']=np.array([di[d] for d in v['date']])
    F[code]=f
    ok=np.isfinite(rv)
    rv_mat[ci,f['idx'][ok]]=rv[ok]
# 市場の出来高レジーム: 全銘柄RVの日次クロスセクション中央値
mkt_rv=np.nanmedian(rv_mat,axis=0)
mkt_rv10=np.array([np.nanmedian(mkt_rv[max(0,i-9):i+1]) for i in range(T)])
pickle.dump(dict(F=F,codes=codes,alldates=alldates,mkt_rv=mkt_rv,mkt_rv10=mkt_rv10),
            open('/tmp/feat.pkl','wb'))
print('T=',T, 'mkt_rv range', np.nanmin(mkt_rv).round(3), np.nanmax(mkt_rv).round(3))
print('mkt_rv10 tail', np.round(mkt_rv10[-8:],3))

# --- step C: セットアップ抽出 + outcome ---
import pickle, numpy as np
D=pickle.load(open('/tmp/feat.pkl','rb')); F=D['F']; mkt_rv10=D['mkt_rv10']; mkt_rv=D['mkt_rv']
WAIT,POST,STOP,BROKEN=20,10,0.05,0.10
recs=[]
for code,f in F.items():
    c=f['close']; lo=f['low']; n=len(c); idx=f['idx']
    lmkt=np.log(np.where(np.isfinite(mkt_rv[idx])&(mkt_rv[idx]>0),mkt_rv[idx],np.nan))
    lrv=np.log(np.where(np.isfinite(f['rv'])&(f['rv']>0),f['rv'],np.nan))
    last=-99
    for t in range(250,n-WAIT-POST):
        if t-last<10: continue
        ma50,ma200,h250,l250,vma=f['ma50'][t],f['ma200'][t],f['h250'][t],f['l250'][t],f['vma50'][t]
        if not np.isfinite([ma50,ma200,h250,l250,vma]).all() or vma<=0: continue
        if not(c[t]>ma50>ma200): continue
        if not(f['ma200'][t]>f['ma200'][t-21]): continue
        if c[t]<0.75*h250 or c[t]<1.3*l250: continue
        if c[t]*vma<5e7: continue
        piv=f['h20'][t]; dist=(piv-c[t])/c[t]
        if not(0<dist<=0.15): continue
        absd=f['volmed10'][t]/vma
        if not np.isfinite(absd): continue
        reld=absd/mkt_rv10[t]
        # 出来高ベータ回帰の残差z (過去250日)
        w=slice(t-249,t+1); x=lmkt[w]; y=lrv[w]; m=np.isfinite(x)&np.isfinite(y)
        rz=np.nan
        if m.sum()>150:
            b,a=np.polyfit(x[m],y[m],1); r=y-(a+b*x)
            rr=r[m]; s=rr.std()
            if s>0: rz=(np.nanmean(r[-10:])-rr.mean())/s
        # outcome
        out=None; boi=None
        for j in range(t+1,min(t+1+WAIT,n)):
            if c[j]>piv: out='breakout'; boi=j; break
            if c[j]<piv*(1-BROKEN): out='broken'; break
        if out is None: out='expired'
        if out=='breakout':
            e=c[boi]; st=e*(1-STOP); out='breakout_ok'
            for j in range(boi+1,min(boi+1+POST,n)):
                if lo[j]<=st: out='breakout_failed'; break
        recs.append((code,t,absd,reld,rz,dist,out))
        last=t
pickle.dump(recs,open('/tmp/recs.pkl','wb'))
print('n=',len(recs))
from collections import Counter; print(Counter(r[6] for r in recs))

# --- step D/E/F: 比較 ---
import pickle,numpy as np
recs=pickle.load(open('/tmp/recs.pkl','rb'))
D=pickle.load(open('/tmp/feat.pkl','rb')); mkt10=D['mkt_rv10']
F=D['F']
codes=np.array([r[0] for r in recs]); ts=np.array([r[1] for r in recs])
A=np.array([r[2] for r in recs]); R=np.array([r[3] for r in recs])
Z=np.array([r[4] for r in recs]); DI=np.array([r[5] for r in recs])
OUT=np.array([r[6] for r in recs])
MK=np.array([mkt10[F[c]['idx'][t]] for c,t in zip(codes,ts)])
y_ok=(OUT=='breakout_ok').astype(int)
y_bo=np.isin(OUT,['breakout_ok','breakout_failed']).astype(int)
def auc(s,y):
    m=np.isfinite(s); s,y=s[m],y[m]
    if y.sum()==0 or y.sum()==len(y): return np.nan
    r=np.argsort(np.argsort(s))+1
    return (r[y==1].sum()-y.sum()*(y.sum()+1)/2)/(y.sum()*(len(y)-y.sum()))
print('=== 汚染チェック: 絶対枯れ vs 市場出来高レジーム ===')
print('corr(abs_dryup, market_rv10) =', round(np.corrcoef(A,MK)[0,1],3))
print('market_rv10 分布 p5/p50/p95 =', np.round(np.nanpercentile(mkt10[~np.isnan(mkt10)],[5,50,95]),3))
print('絶対枯れ<=0.77 の該当率: 市場薄い日 vs 厚い日 =',
      round((A[MK<np.median(MK)]<=0.77).mean(),3), round((A[MK>=np.median(MK)]<=0.77).mean(),3))
print()
print('=== 予測力 (低いほど良い指標なので符号反転してAUC) ===')
for nm,s in [('絶対枯れ abs_dryup',A),('相対枯れ rel=abs/mkt',R),('残差z resid_z',Z),('(参考)dist_to_pivot',DI)]:
    print(f'{nm:24s} AUC(breakout_ok)={auc(-s,y_ok):.4f}  AUC(ブレイク到達)={auc(-s,y_bo):.4f}')
print()
print('=== 五分位別 breakout_ok 率 (n=%d, base=%.3f) ==='%(len(y_ok),y_ok.mean()))
for nm,s in [('abs',A),('rel',R),('residz',Z)]:
    m=np.isfinite(s); q=np.nanpercentile(s[m],[20,40,60,80]); b=np.digitize(s,q)
    row=[f'Q{i+1}:{y_ok[m&(b==i)].mean():.3f}(n={ (m&(b==i)).sum() })' for i in range(5)]
    print(f'{nm:6s}(枯れ→薄)', ' '.join(row))
import pickle,numpy as np
recs=pickle.load(open('/tmp/recs.pkl','rb'));D=pickle.load(open('/tmp/feat.pkl','rb'))
mkt10=D['mkt_rv10'];F=D['F']
A=np.array([r[2] for r in recs]);R=np.array([r[3] for r in recs]);Z=np.array([r[4] for r in recs])
DI=np.array([r[5] for r in recs]);OUT=np.array([r[6] for r in recs])
MK=np.array([mkt10[F[r[0]]['idx'][r[1]]] for r in recs])
y=(OUT=='breakout_ok').astype(int)
def auc(s,yy):
    m=np.isfinite(s);s,yy=s[m],yy[m]
    if yy.sum() in(0,len(yy)):return np.nan
    r=np.argsort(np.argsort(s))+1
    return (r[yy==1].sum()-yy.sum()*(yy.sum()+1)/2)/(yy.sum()*(len(yy)-yy.sum()))
print('市場レジーム単体 AUC(薄い市場=良い?):',round(auc(-MK,y),4))
print()
print('=== dist_to_pivot 3分割内での AUC(交絡除去) ===')
qd=np.percentile(DI,[33,67]);bd=np.digitize(DI,qd)
print(f"{'dist帯':10s} {'n':>5s} {'base':>6s} {'abs':>7s} {'rel':>7s} {'residz':>7s}")
for i,lab in enumerate(['近い','中','遠い']):
    m=bd==i
    print(f'{lab:10s} {m.sum():5d} {y[m].mean():6.3f} {auc(-A[m],y[m]):7.4f} {auc(-R[m],y[m]):7.4f} {auc(-Z[m],y[m]):7.4f}')
print()
print('=== 2x2: 銘柄の枯れ × 市場レジーム (breakout_ok率) ===')
am=np.median(A);mm=np.median(MK)
for al,ac in [('枯れ(abs低)',A<am),('非枯れ(abs高)',A>=am)]:
    for ml,mc in [('市場薄',MK<mm),('市場厚',MK>=mm)]:
        m=ac&mc;print(f'  {al:14s} × {ml:6s}  n={m.sum():5d}  ok={y[m].mean():.3f}')
print()
print('=== abs_dryup 十分位 (非単調性の確認) ===')
q=np.percentile(A,np.arange(10,100,10));b=np.digitize(A,q)
print(' '.join(f'D{i+1}:{y[b==i].mean():.3f}' for i in range(10)))
print('abs閾値別 ok率: <=0.66 %.3f(n=%d) / 0.66-0.77 %.3f(n=%d) / 0.77-1.0 %.3f(n=%d) / >1.0 %.3f(n=%d)'%(
 y[A<=.66].mean(),(A<=.66).sum(),y[(A>.66)&(A<=.77)].mean(),((A>.66)&(A<=.77)).sum(),
 y[(A>.77)&(A<=1.)].mean(),((A>.77)&(A<=1.)).sum(),y[A>1.].mean(),(A>1.).sum()))
import pickle,numpy as np
recs=pickle.load(open('/tmp/recs.pkl','rb'));D=pickle.load(open('/tmp/feat.pkl','rb'))
mkt10=D['mkt_rv10'];F=D['F'];dates=D['alldates']
A=np.array([r[2] for r in recs]);R=np.array([r[3] for r in recs]);Z=np.array([r[4] for r in recs])
OUT=np.array([r[6] for r in recs]);y=(OUT=='breakout_ok').astype(int)
gi=np.array([F[r[0]]['idx'][r[1]] for r in recs]);MK=mkt10[gi]
def auc(s,yy):
    m=np.isfinite(s);s,yy=s[m],yy[m];r=np.argsort(np.argsort(s))+1
    return (r[yy==1].sum()-yy.sum()*(yy.sum()+1)/2)/(yy.sum()*(len(yy)-yy.sum()))
half=np.median(gi)
print('=== 期間前半/後半の安定性 (AUC) ===')
for lab,m in [('前半 '+dates[int(gi[gi<half].max())][:7],gi<half),('後半 '+dates[-1][:7],gi>=half)]:
    print(f'{lab}  n={m.sum():5d} base={y[m].mean():.3f}  abs={auc(-A[m],y[m]):.4f} rel={auc(-R[m],y[m]):.4f} residz={auc(-Z[m],y[m]):.4f} mkt={auc(-MK[m],y[m]):.4f}')
def se(m): p=y[m].mean();return (p*(1-p)/m.sum())**.5
print()
print('=== 激枯れ(<=0.66) は本当に劣後か ===')
for lab,m in [('<=0.66',A<=.66),('0.66-0.77',(A>.66)&(A<=.77))]:
    print(f'  {lab:10s} ok={y[m].mean():.3f} ±{1.96*se(m):.3f} (n={m.sum()})')
print()
print('=== 相互作用: 市場厚い日のみ abs が効く ===')
mm=np.median(MK)
for lab,m in [('市場薄',MK<mm),('市場厚',MK>=mm)]:
    print(f'  {lab} abs AUC={auc(-A[m],y[m]):.4f} (n={m.sum()})')
