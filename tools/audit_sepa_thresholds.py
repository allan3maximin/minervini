"""SEPA骨格7閾値の「日本にアジャストできているか」診断スクリプト(2026-07-28)。

方針: 最適値は探索しない(=カーブフィット回避)。各閾値について
(a)東証実分布での位置=何%を落としているか (b)原変数の単調性 (c)期間前半/後半の符号保存
の3点のみ出す。機構仮説はデータを見る前に事前登録済み(下記)。

★最重要の副産物: 既存の outcome 定義(-5%不通過の二値)はヒット率であって期待値ではなく、
  高ボラ銘柄を機械的に罰する。結論は log.md 2026-07-28(134) 参照。
"""
# 【事前登録: 機構仮説】(データを見る前に記述。2026-07-28)
# 
# H1 rs_min=70
#   日本は先物バスケット/指数寄与度で全セクターが同時に動く比率が高く、個別リターンの
#   クロスセクション分散が米国より小さい。加えて母集団が「売買代金1億円以上・約2000銘柄」で
#   IBDの全上場約8000とは別物。同じ「上位30%」でも意味が違う。
#   → 予測: RS帯の傾きは正だが緩い。70という切り方の識別力は米国ほど鋭くない。
# 
# H2 high52w_margin=0.75 (高値25%以内)
#   日本は値幅制限と単元株で急騰しにくく、52週高値更新のブレッドスが構造的に薄い。
#   → 予測: 高値からの距離は狭い側に固まり、25%という閾値はほとんど誰も落とさない
#     =自由度を使っているのに効いていない条件の疑い。
# 
# H3 low52w_margin=1.25 (安値比1.25倍以上)
#   Minerviniの1.30から既に緩めてあるがコメントなし=出所不明。日本は年間上昇率が
#   構造的に小さいので緩める方向自体は合理的。
#   → 予測: これも実質的に誰も落としていない可能性。何%落としているかを測る。
# 
# H4 ma200_up_days_min=21
#   MA200の傾きは市場を問わず同じ意味を持つはずで、機構差の理由が薄い。ただし日本は
#   長期横ばいが長くMA200がフラット張り付きする銘柄が多い可能性。
#   → 予測: 効かないか、緩い単調性。日本固有調整の根拠は出ないと予想。
# 
# H5 breakout_vol_mult=1.4  ★最も日本固有差が出るはず
#   値幅制限(ストップ高/売り気配)があるとブレイク日の出来高が制度的に切られる。
#   強いブレイクほど板が飛んで商いが成立せず、出来高倍率が「出ない」逆転が起きうる。
#   単元100株・個人主体で倍率のノイズも大きい。
#   → 予測: 1.4倍の識別力が米国ほど出ない。最上位ブレイクで倍率が下がる非単調の可能性。
# 
# H6 stop_loss_pct=0.05
#   Minerviniの5-6%は米国株のボラ前提。日本の中小型はATR/closeが高い帯がある。
#   → 予測: -5%固定は日本の高ボラ帯で「正常な揺れ」で刈られる。勝ちトレードのMAE分布で
#     -5%が何割の勝ちを刈っているかを見る。
# 
# H7 extended_pct=0.05
#   日本は値幅制限でギャップアップしやすく、pivotから5%以内で買える機会が構造的に少ない
#   可能性がある(気配のまま飛ぶ)。
#   → 予測: ブレイク日にpivot+5%以内に収まる比率が低い=多くの正解を制度的に見送っている。
# 
# 【検証方針】最適値は探さない。各閾値について (a)東証実分布での位置(何%を落としているか)
# (b)原変数のバケット別成績が単調か (c)期間前半/後半で符号が保たれるか の3点のみ出す。

# --- RS(既存 add_rs_raw / rs_percentile_rank と同一定義) ---
import pickle, numpy as np
D=pickle.load(open('/tmp/feat.pkl','rb')); F=D['F']; T=len(D['alldates']); codes=D['codes']
LB=[63,126,189,252]; W=[2.,1.,1.,1.]
rs_mat=np.full((len(codes),T),np.nan)
for ci,code in enumerate(codes):
    c=F[code]['close']; tot=np.zeros(len(c)); ok=np.ones(len(c),bool)
    for lb,w in zip(LB,W):
        sh=np.full(len(c),np.nan); sh[lb:]=c[:-lb]
        with np.errstate(all='ignore'): tot=tot+w*(c/sh-1.0)
        ok &= np.isfinite(sh)&(sh>0)
    F[code]['rs_raw']=np.where(ok,tot,np.nan)
    m=ok; rs_mat[ci,F[code]['idx'][m]]=tot[m]
# 日次クロスセクションでRS 1-99 に変換(既存 rs_percentile_rank と同一定義)
rs_pct=np.full_like(rs_mat,np.nan)
for t in range(T):
    col=rs_mat[:,t]; m=np.isfinite(col)
    if m.sum()<50: continue
    r=col[m].argsort().argsort()+1
    # average rank for ties は近似(同値はほぼ無い)
    rs_pct[np.where(m)[0],t]=np.clip(np.round(r/m.sum()*98+1),1,99)
for ci,code in enumerate(codes):
    F[code]['rs']=rs_pct[ci,F[code]['idx']]
pickle.dump(D,open('/tmp/feat2.pkl','wb'))
print('rs ok. sample dist p10/50/90 of universe size per day:',
      np.percentile([np.isfinite(rs_pct[:,t]).sum() for t in range(T)],[10,50,90]))

# --- 緩和セットアップ抽出 ---
import pickle, numpy as np
D=pickle.load(open('/tmp/feat2.pkl','rb')); F=D['F']
WAIT,POST=20,10
R=[]
for code,f in F.items():
    c=f['close']; hi=f['high']; lo=f['low']; n=len(c)
    pc=np.concatenate(([c[0]],c[:-1]))
    tr=np.maximum(hi-lo,np.maximum(abs(hi-pc),abs(lo-pc)))
    atr=np.full(n,np.nan)
    cs=np.concatenate(([0.],np.cumsum(tr))); atr[13:]=(cs[14:]-cs[:-14])/14
    last=-99
    for t in range(260,n-WAIT-POST):
        if t-last<10: continue
        ma200,ma50,vma=f['ma200'][t],f['ma50'][t],f['vma50'][t]
        if not np.isfinite([ma200,ma50,vma,f['h250'][t],f['l250'][t],f['rs'][t],atr[t]]).all(): continue
        if c[t]<=ma200: continue                     # ステージ2の最低限のみ
        if c[t]*vma<1e8: continue                    # config universe 準拠
        piv=f['h20'][t]; dist=(piv-c[t])/c[t]
        if not(0<dist<=0.15): continue
        rec=dict(code=code,t=t,rs=f['rs'][t],
                 hr=c[t]/f['h250'][t], lr=c[t]/f['l250'][t],
                 ma200sl=f['ma200'][t]/f['ma200'][t-21]-1.0,
                 above50=c[t]>ma50, atrp=atr[t]/c[t],
                 dryup=f['volmed10'][t]/vma, dist=dist, piv=piv)
        # outcome
        out='expired'; boi=None
        for j in range(t+1,min(t+1+WAIT,n)):
            if c[j]>piv: out='breakout'; boi=j; break
            if c[j]<piv*0.90: out='broken'; break
        rec['bo']=out
        if out=='breakout':
            e=c[boi]
            rec['bo_vol']=f['vol'][boi]/f['vma50'][boi] if np.isfinite(f['vma50'][boi]) and f['vma50'][boi]>0 else np.nan
            rec['bo_gap']=(e-piv)/piv                      # pivotからの超過(=extended度)
            post=slice(boi+1,min(boi+1+POST,n))
            pl=lo[post]; ph=hi[post]; pcl=c[post]
            rec['mae']=(e-pl.min())/e if len(pl) else np.nan   # 最大逆行
            rec['mfe']=(ph.max()-e)/e if len(ph) else np.nan   # 最大順行
            rec['ret10']=(pcl[-1]-e)/e if len(pcl) else np.nan
            rec['ok']=1 if (len(pl) and pl.min()>e*0.95) else 0   # -5%不通過=既存定義のok
        R.append(rec); last=t
pickle.dump(R,open('/tmp/relaxed.pkl','wb'))
print('n=',len(R),' breakout=',sum(1 for r in R if r['bo']=='breakout'))

# --- 診断(旧ok基準。交絡あり・記録用) ---
import pickle,numpy as np
R=pickle.load(open('/tmp/relaxed.pkl','rb'))
D=pickle.load(open('/tmp/feat2.pkl','rb'));F=D['F']
g=lambda k: np.array([r.get(k,np.nan) for r in R],dtype='f8')
gi=np.array([F[r['code']]['idx'][r['t']] for r in R]); half=np.median(gi)
BO=np.array([r['bo'] for r in R])
# 「成功」= ブレイク到達 かつ 直後10日で-5%不通過 (既存 dryup_log 定義と同一)
ok=np.array([r.get('ok',0) if r['bo']=='breakout' else 0 for r in R],dtype=float)
def rep(name,x,edges,note=''):
    print(f'\n--- {name} {note}')
    b=np.digitize(x,edges); lab=[f'<{edges[0]:g}']+[f'{edges[i]:g}-{edges[i+1]:g}' for i in range(len(edges)-1)]+[f'>={edges[-1]:g}']
    for i in range(len(edges)+1):
        m=(b==i)&np.isfinite(x)
        if m.sum()<50: print(f'  {lab[i]:>12s} n={m.sum():5d} (少)'); continue
        h1=m&(gi<half); h2=m&(gi>=half)
        print(f'  {lab[i]:>12s} n={m.sum():5d}  ok={ok[m].mean():.3f}  [前半 {ok[h1].mean() if h1.sum()>30 else float("nan"):.3f} / 後半 {ok[h2].mean() if h2.sum()>30 else float("nan"):.3f}]')
print('全体 n=%d  base ok率=%.3f  (ブレイク到達率=%.3f)'%(len(R),ok.mean(),(BO=='breakout').mean()))
print('\n================ H1 rs_min=70 ================')
rs=g('rs'); print(f'現閾値70が落とす割合: {(rs<70).mean():.1%}  (RS分布 p10/50/90={np.nanpercentile(rs,[10,50,90]).round(0)})')
rep('RS','rs' and rs,[30,50,70,85])
print('\n================ H2 high52w_margin=0.75 (高値25%以内) ================')
hr=g('hr'); print(f'現閾値0.75が落とす割合: {(hr<0.75).mean():.1%}  (close/52w高 p10/50/90={np.nanpercentile(hr,[10,50,90]).round(3)})')
rep('close/52w高',hr,[0.75,0.85,0.92,0.97])
print('\n================ H3 low52w_margin=1.25 ================')
lr=g('lr'); print(f'現閾値1.25が落とす割合: {(lr<1.25).mean():.1%} / Minervini原典1.30なら {(lr<1.30).mean():.1%}  (p10/50/90={np.nanpercentile(lr,[10,50,90]).round(2)})')
rep('close/52w安',lr,[1.25,1.5,2.0,3.0])
print('\n================ H4 ma200_up_days_min=21 ================')
sl=g('ma200sl'); print(f'現条件(21日前比プラス)が落とす割合: {(sl<=0).mean():.1%}  (傾き p10/50/90={np.nanpercentile(sl,[10,50,90]).round(4)})')
rep('MA200の21日傾き',sl,[0,0.01,0.03,0.06])
import pickle,numpy as np
R=pickle.load(open('/tmp/relaxed.pkl','rb'));D=pickle.load(open('/tmp/feat2.pkl','rb'));F=D['F']
B=[r for r in R if r['bo']=='breakout']
gi=np.array([F[r['code']]['idx'][r['t']] for r in B]);half=np.median(gi)
g=lambda k:np.array([r.get(k,np.nan) for r in B],dtype='f8')
ok=g('ok');bv=g('bo_vol');gap=g('bo_gap');mae=g('mae');mfe=g('mfe');ret=g('ret10');atrp=g('atrp')
print('ブレイク到達 n=%d  ok率=%.3f'%(len(B),np.nanmean(ok)))
def rep(x,edges,lab0):
    b=np.digitize(x,edges);L=[f'<{edges[0]:g}']+[f'{edges[i]:g}-{edges[i+1]:g}' for i in range(len(edges)-1)]+[f'>={edges[-1]:g}']
    for i in range(len(edges)+1):
        m=(b==i)&np.isfinite(x)
        if m.sum()<50: print(f'  {L[i]:>12s} n={m.sum():5d} (少)');continue
        h1=m&(gi<half);h2=m&(gi>=half)
        print(f'  {L[i]:>12s} n={m.sum():5d} ok={np.nanmean(ok[m]):.3f} 10日リタン={np.nanmean(ret[m]):+.3f} [前{np.nanmean(ok[h1]):.3f}/後{np.nanmean(ok[h2]):.3f}]')
print('\n=========== H5 breakout_vol_mult=1.4 ===========')
print(f'ブレイク日出来高倍率 p10/25/50/75/90 = {np.nanpercentile(bv,[10,25,50,75,90]).round(2)}')
print(f'1.4倍未満で落とす割合: {np.nanmean(bv<1.4):.1%}')
rep(bv,[1.0,1.4,2.0,3.0],'')
print('\n=========== H6 stop_loss_pct=0.05 ===========')
print(f'ブレイク後10日の最大逆行MAE  p25/50/75/90 = {np.nanpercentile(mae,[25,50,75,90]).round(3)}')
win=(mfe>=0.10)  # 10日以内に+10%以上取れた=本来の勝ち
print(f'「10日で+10%到達」した勝ち組 n={np.nansum(win):.0f} ({np.nanmean(win):.1%})')
for s in [0.05,0.06,0.07,0.08,0.10]:
    print(f'  ストップ -{s:.0%}: 勝ち組のうち先に刈られる割合 = {np.nanmean(mae[win]>s):.1%}')
print('  ATR比で見た-5%の位置:')
for lo,hi,lab in [(0,.02,'低ボラ ATR<2%'),(.02,.035,'中 2-3.5%'),(.035,.06,'高 3.5-6%'),(.06,9,'激高 >6%')]:
    m=(atrp>=lo)&(atrp<hi)&win
    if m.sum()<30: continue
    print(f'    {lab:14s} n={m.sum():4d}  -5%で刈られる={np.nanmean(mae[m]>0.05):.1%}  -5%はATRの{0.05/np.nanmean(atrp[m]):.1f}倍')
print('\n=========== H7 extended_pct=0.05 ===========')
print(f'ブレイク日のpivot超過率 p25/50/75/90 = {np.nanpercentile(gap,[25,50,75,90]).round(3)}')
print(f'5%超で見送る割合: {np.nanmean(gap>0.05):.1%}')
rep(gap,[0.01,0.03,0.05,0.10],'')

# --- 診断(ATR正規化した期待R基準。こちらが本命) ---
import pickle,numpy as np
R=pickle.load(open('/tmp/relaxed.pkl','rb'));D=pickle.load(open('/tmp/feat2.pkl','rb'));F=D['F']
B=[r for r in R if r['bo']=='breakout']
gi=np.array([F[r['code']]['idx'][r['t']] for r in B]);half=np.median(gi)
g=lambda k:np.array([r.get(k,np.nan) for r in B],dtype='f8')
mae,mfe,ret,atrp=g('mae'),g('mfe'),g('ret10'),g('atrp')
ok=g('ok')
# ATR正規化ストップ: 1.5*ATR/close を 3%〜12% でクリップ
sd=np.clip(1.5*atrp,0.03,0.12)
Rmult=np.where(mae>sd,-1.0,ret/sd)   # 刈られたら-1R、生きてたら10日リターン/ストップ幅
print('ストップ幅の分布 p10/50/90 = %s  (固定5%%との比較)'%np.round(np.nanpercentile(sd,[10,50,90]),3))
print('全ブレイク n=%d  平均R(ATR正規化)=%.3f  参考:固定5%%ok率=%.3f'%(len(B),np.nanmean(Rmult),np.nanmean(ok)))
def rep(nm,x,edges):
    print(f'\n--- {nm}')
    b=np.digitize(x,edges);L=[f'<{edges[0]:g}']+[f'{edges[i]:g}-{edges[i+1]:g}' for i in range(len(edges)-1)]+[f'>={edges[-1]:g}']
    for i in range(len(edges)+1):
        m=(b==i)&np.isfinite(x)&np.isfinite(Rmult)
        if m.sum()<50: print(f'  {L[i]:>12s} n={m.sum():5d} (少)');continue
        h1=m&(gi<half);h2=m&(gi>=half)
        print(f'  {L[i]:>12s} n={m.sum():5d}  期待R={np.nanmean(Rmult[m]):+.3f}  ATR={np.nanmean(atrp[m]):.3f}  旧ok={np.nanmean(ok[m]):.3f}  [前R{np.nanmean(Rmult[h1]):+.2f}/後R{np.nanmean(Rmult[h2]):+.2f}]')
rep('H1 RS',g('rs'),[30,50,70,85])
rep('H2 close/52w高',g('hr'),[0.75,0.85,0.92,0.97])
rep('H3 close/52w安',g('lr'),[1.25,1.5,2.0,3.0])
rep('H4 MA200 21日傾き',g('ma200sl'),[0,0.01,0.03,0.06])
rep('H5 ブレイク日出来高倍率',g('bo_vol'),[1.0,1.4,2.0,3.0])
rep('H7 pivot超過率',g('bo_gap'),[0.01,0.03,0.05,0.10])
rep('(再検証)枯れ度 dryup',g('dryup'),[0.66,0.77,1.0])

# --- ストップ定義の感度(結論がストップの決め方に依存しないかの確認) ---
import pickle,numpy as np
R=pickle.load(open('/tmp/relaxed.pkl','rb'));D=pickle.load(open('/tmp/feat2.pkl','rb'));F=D['F']
B=[r for r in R if r['bo']=='breakout']
g=lambda k:np.array([r.get(k,np.nan) for r in B],dtype='f8')
mae,ret,atrp=g('mae'),g('ret10'),g('atrp')
defs={'1.5ATR(3-12%clip)':np.clip(1.5*atrp,0.03,0.12),'1.5ATR(clipなし)':1.5*atrp,
      '2.0ATR(clipなし)':2.0*atrp,'固定5%':np.full_like(atrp,0.05),'固定8%':np.full_like(atrp,0.08)}
vars_={'H1 RS':(g('rs'),[70,85]),'H3 52w安倍率':(g('lr'),[2.0,3.0]),
       'H4 MA200傾き':(g('ma200sl'),[0.03,0.06]),'H5 ブレイク出来高':(g('bo_vol'),[1.4,3.0]),
       'H7 pivot超過':(g('bo_gap'),[0.05,0.10]),'枯れ度':(g('dryup'),[0.66,0.77])}
print('【ストップ定義の感度】各変数の「高い側 vs 低い側」の期待R差 (高-低)。符号が定義で反転しないか。')
print(f"{'':22s}"+''.join(f'{k:>20s}' for k in defs))
for nm,(x,e) in vars_.items():
    row=f'{nm:22s}'
    for k,sd in defs.items():
        Rm=np.where(mae>sd,-1.0,ret/sd)
        hi=x>=e[-1]; lo=x<e[0]; m=np.isfinite(x)&np.isfinite(Rm)
        d=np.nanmean(Rm[hi&m])-np.nanmean(Rm[lo&m])
        row+=f'{d:+20.3f}'
    print(row)
print('\n【生リターン(正規化なし)での方向確認】10日リターン平均')
for nm,(x,e) in vars_.items():
    m=np.isfinite(x)&np.isfinite(ret)
    print(f'  {nm:22s} 低側{np.nanmean(ret[(x<e[0])&m]):+.4f}  高側{np.nanmean(ret[(x>=e[-1])&m]):+.4f}  差{np.nanmean(ret[(x>=e[-1])&m])-np.nanmean(ret[(x<e[0])&m]):+.4f}')
print('\n【念のため】固定5%ストップは何を測っているか: ATRとの相関')
sd=np.full_like(atrp,0.05); Rm=np.where(mae>sd,-1.0,ret/sd)
okv=(mae<=0.05).astype(float)
print(f'  corr(旧ok, ATR/close) = {np.corrcoef(okv[np.isfinite(atrp)],atrp[np.isfinite(atrp)])[0,1]:+.3f}')
print(f'  corr(10日リターン, ATR/close) = {np.corrcoef(ret[np.isfinite(atrp)&np.isfinite(ret)],atrp[np.isfinite(atrp)&np.isfinite(ret)])[0,1]:+.3f}')
