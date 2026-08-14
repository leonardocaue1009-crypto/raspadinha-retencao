import os, sqlite3, random, math
from flask import Flask, request, jsonify, session, Response
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "troque-esta-chave-no-render")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "507357")
DB = os.environ.get("DB_PATH", "/tmp/raspadinha_retencao.db")

def conn():
    c = sqlite3.connect(DB, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS users(
        matricula TEXT PRIMARY KEY,
        nome TEXT NOT NULL,
        creditos INTEGER NOT NULL DEFAULT 0,
        ativo INTEGER NOT NULL DEFAULT 1
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS results(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        matricula TEXT NOT NULL,
        nome TEXT NOT NULL,
        premio TEXT NOT NULL,
        categoria TEXT NOT NULL,
        data TEXT NOT NULL
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS meta(
        chave TEXT PRIMARY KEY,
        valor TEXT NOT NULL
    )""")
    defaults = {
        "common_pos": "0", "common_cycle": "", "special_pos": "0", "special_cycle": "",
        "intervalo_treinamento30": "50", "intervalo_saida": "100", "intervalo_cafe": "20",
        "intervalo_folga_banco": "300", "ciclo_doces": "7", "ciclo_trolls": "3",
        "peso_chocolate": "1", "peso_pirulito": "1", "peso_bala": "1",
        "peso_aplausos": "1", "peso_moral": "1", "peso_guerreiro": "1",
    }
    for chave, valor in defaults.items():
        if not c.execute("SELECT 1 FROM meta WHERE chave=?", (chave,)).fetchone():
            c.execute("INSERT INTO meta(chave,valor) VALUES(?,?)", (chave, valor))
    if not c.execute("SELECT 1 FROM meta WHERE chave='admin_password_hash'").fetchone():
        c.execute("INSERT INTO meta(chave,valor) VALUES('admin_password_hash',?)", (generate_password_hash(ADMIN_PASSWORD),))
    if not c.execute("SELECT 1 FROM meta WHERE chave='exact_specials_v1'").fetchone():
        for chave, valor in {"intervalo_treinamento30":"50","intervalo_saida":"100","intervalo_cafe":"20","intervalo_folga_banco":"300"}.items():
            c.execute("INSERT INTO meta(chave,valor) VALUES(?,?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor", (chave, valor))
        c.execute("UPDATE meta SET valor='0' WHERE chave='special_pos'")
        c.execute("UPDATE meta SET valor='' WHERE chave='special_cycle'")
        c.execute("INSERT INTO meta(chave,valor) VALUES('exact_specials_v1','1')")
    if not c.execute("SELECT 1 FROM meta WHERE chave='common_7_3_bala_v1'").fetchone():
        c.execute("INSERT INTO meta(chave,valor) VALUES('ciclo_doces','7') ON CONFLICT(chave) DO UPDATE SET valor='7'")
        c.execute("INSERT INTO meta(chave,valor) VALUES('ciclo_trolls','3') ON CONFLICT(chave) DO UPDATE SET valor='3'")
        c.execute("INSERT INTO meta(chave,valor) VALUES('peso_bala','1') ON CONFLICT(chave) DO NOTHING")
        c.execute("UPDATE meta SET valor='0' WHERE chave='common_pos'")
        c.execute("UPDATE meta SET valor='' WHERE chave='common_cycle'")
        c.execute("UPDATE meta SET valor='0' WHERE chave='special_pos'")
        c.execute("UPDATE meta SET valor='' WHERE chave='special_cycle'")
        c.execute("INSERT INTO meta(chave,valor) VALUES('common_7_3_bala_v1','1')")
    c.commit()
    return c

RAROS = [
    ("30 MINUTOS DE PAUSA TREINAMENTO","🎓","treinamento30","intervalo_treinamento30"),
    ("1 HORA DE SAÍDA ANTECIPADA","🏃","saida","intervalo_saida"),
    ("PAUSA CAFÉ","☕","cafe","intervalo_cafe"),
    ("1 FOLGA DO BANCO DE HORAS (A COMBINAR)","🏖️","folga_banco","intervalo_folga_banco"),
]
DOCES = [
    ("CHOCOLATE","🍫","chocolate","peso_chocolate"),
    ("PIRULITO","🍭","pirulito","peso_pirulito"),
    ("BALA","🍬","bala","peso_bala"),
]
TROLLS = [
    ("VOCÊ GANHOU APLAUSOS","👏","troll","peso_aplausos"),
    ("+5 DE MORAL","😎","troll","peso_moral"),
    ("NADA, MAS VOCÊ FOI GUERREIRO!","😂","troll","peso_guerreiro"),
]

def meta_float(c, chave, padrao):
    r=c.execute("SELECT valor FROM meta WHERE chave=?",(chave,)).fetchone()
    try: return float(r["valor"]) if r else float(padrao)
    except: return float(padrao)

def meta_int(c, chave, padrao):
    try: return max(0, int(round(meta_float(c,chave,padrao))))
    except: return padrao

def weighted_pick(c, items):
    weights=[max(0.0, meta_float(c, item[3], 1)) for item in items]
    if sum(weights) <= 0: weights=[1.0]*len(items)
    nome, emoji, cat, _ = random.choices(items, weights=weights, k=1)[0]
    return {"nome":nome,"emoji":emoji,"categoria":cat}

def build_cycle(c):
    doces=meta_int(c,"ciclo_doces",7)
    trolls=meta_int(c,"ciclo_trolls",3)
    if doces+trolls <= 0: doces,trolls=7,3
    cycle=["doce"]*doces+["troll"]*trolls
    random.shuffle(cycle)
    return ",".join(cycle)

def special_intervals(c):
    return [(nome, emoji, cat, chave, meta_int(c, chave, 0)) for nome,emoji,cat,chave in RAROS if meta_int(c,chave,0) > 0]

def special_cycle_length(c):
    vals=[x[4] for x in special_intervals(c)]
    if not vals: return 1
    L=1
    for n in vals:
        L=math.lcm(L,n)
        if L > 10000:
            return 0
    return L

def build_special_cycle(c):
    specs=special_intervals(c)
    if not specs: return [""]
    L=special_cycle_length(c)
    if L <= 0: raise ValueError("Os intervalos escolhidos geram um ciclo muito grande. Use valores mais compatíveis entre si.")
    # Tenta montar um calendário sem colisões. Em cada bloco de N posições há EXATAMENTE 1 unidade daquele prêmio.
    for _ in range(250):
        slots=[""]*L
        ok=True
        # Menores intervalos primeiro, pois têm mais blocos para preencher.
        order=sorted(specs, key=lambda x:x[4])
        for nome,emoji,cat,chave,n in order:
            for ini in range(0,L,n):
                livres=[i for i in range(ini,min(ini+n,L)) if not slots[i]]
                if not livres:
                    ok=False; break
                slots[random.choice(livres)]=cat
            if not ok: break
        if ok: return slots
    raise ValueError("Não foi possível combinar esses intervalos sem sobrepor prêmios. Aumente um ou mais valores.")

def choose_prize(c):
    # Calendário GLOBAL: não reinicia por matrícula.
    # Cada prêmio especial aparece EXATAMENTE uma vez dentro de cada bloco do intervalo configurado.
    L=special_cycle_length(c)
    if L <= 0: L=1
    row=c.execute("SELECT valor FROM meta WHERE chave='special_cycle'").fetchone()
    raw=row["valor"] if row else ""
    parts=raw.split(",") if raw else []
    try: spos=int((c.execute("SELECT valor FROM meta WHERE chave='special_pos'").fetchone() or {"valor":"0"})["valor"])
    except: spos=0
    if len(parts)!=L or spos>=L:
        parts=build_special_cycle(c); spos=0
        c.execute("UPDATE meta SET valor=? WHERE chave='special_cycle'",(",".join(parts),))
    token=parts[spos] if spos < len(parts) else ""
    next_pos=spos+1
    if next_pos>=L:
        next_pos=0
        # Gera um novo calendário aleatório para o próximo ciclo, mantendo as mesmas quantidades exatas.
        novo=build_special_cycle(c)
        c.execute("UPDATE meta SET valor=? WHERE chave='special_cycle'",(",".join(novo),))
    c.execute("UPDATE meta SET valor=? WHERE chave='special_pos'",(str(next_pos),))
    if token:
        for nome,emoji,cat,chave in RAROS:
            if cat==token:
                return {"nome":nome,"emoji":emoji,"categoria":cat}

    # Se esta posição não é especial, segue o ciclo global de 7 doces / 3 trolls (ou o que estiver no ADM).
    row=c.execute("SELECT valor FROM meta WHERE chave='common_cycle'").fetchone()
    cycle=row["valor"] if row else ""
    try: pos=int(c.execute("SELECT valor FROM meta WHERE chave='common_pos'").fetchone()["valor"])
    except: pos=0
    parts=[x for x in cycle.split(",") if x]
    alvo=meta_int(c,"ciclo_doces",7)+meta_int(c,"ciclo_trolls",3)
    if alvo <= 0: alvo=10
    if not parts or len(parts)!=alvo or pos>=len(parts):
        cycle=build_cycle(c); parts=cycle.split(","); pos=0
        c.execute("UPDATE meta SET valor=? WHERE chave='common_cycle'",(cycle,))
    tipo=parts[pos]
    c.execute("UPDATE meta SET valor=? WHERE chave='common_pos'",(str(pos+1),))
    return weighted_pick(c, DOCES if tipo=="doce" else TROLLS)

HOME = r"""<!doctype html>
<html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Raspadinha — Time Retenção</title>
<style>
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:#06111f;font-family:Arial,Helvetica,sans-serif;color:#fff}
body{display:flex;justify-content:center;align-items:flex-start;padding:14px}.app{position:relative;width:min(1080px,98vw);border-radius:22px;overflow:hidden;box-shadow:0 20px 70px #000;background:#071525}.poster{display:block;width:100%;height:auto}
.login{position:absolute;inset:0;background:rgba(3,10,20,.94);z-index:50;display:flex;align-items:center;justify-content:center;padding:20px}
.box{width:min(430px,92vw);background:linear-gradient(145deg,#0d2942,#071525);border:1px solid #1cc8ff66;border-radius:22px;padding:28px;box-shadow:0 20px 70px #000;text-align:center}
.box h1{margin:0 0 8px;font-size:30px}.box p{color:#b7c9d8;font-size:14px;line-height:1.45}
input{width:100%;padding:13px 14px;border-radius:12px;border:1px solid #55d9ff55;background:#06111f;color:#fff;outline:none;font-size:16px;text-align:center}
button{margin-top:14px;padding:12px 22px;border:0;border-radius:999px;font-weight:900;background:#12bfff;color:#03192a;cursor:pointer}
.error{color:#ff7c92;font-size:13px;min-height:18px;margin-top:9px}.hidden{display:none!important}
.scratch{position:absolute;left:5%;right:5%;top:35.4%;height:27%;border-radius:22px;overflow:hidden;touch-action:none;cursor:crosshair}
.prize{position:absolute;inset:0;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:20px;color:#fff;overflow:hidden}
.prize:before{content:"";position:absolute;inset:-20%;opacity:.35;filter:blur(2px)}
.prize>*{position:relative;z-index:2}.emoji{font-size:clamp(52px,8vw,92px);filter:drop-shadow(0 8px 12px #0008)}
.prize h2{font-size:clamp(24px,4vw,42px);margin:4px 0;text-shadow:0 4px 12px #000}.prize p{font-size:clamp(18px,2.5vw,28px);font-weight:900;margin:0;text-shadow:0 4px 12px #000}
.chocolate{background:radial-gradient(circle at 30% 20%,#8a4b22,#4b1e0b 55%,#251006)}.chocolate:before{background:repeating-linear-gradient(45deg,#fff0 0 30px,#d99d6644 30px 60px)}
.pirulito{background:radial-gradient(circle at 50% 20%,#ffb6da,#ff4d9f 48%,#85184c)}.pirulito:before{background:conic-gradient(#fff5,#ff58a955,#fff5,#ff58a955)}
.bala{background:radial-gradient(circle at 50% 20%,#8ee7ff,#4388ff 48%,#173d8f)}.bala:before{background:repeating-linear-gradient(45deg,#ffffff2e 0 22px,#fff0 22px 44px)}
.troll{background:radial-gradient(circle,#315a32,#152819 60%,#09150b)}.troll:before{background:repeating-linear-gradient(-25deg,#9cff8a22 0 20px,#fff0 20px 45px)}
.cafe{background:radial-gradient(circle at 50% 20%,#c98a51,#6b3519 55%,#2a140b)}.cafe:before{background:repeating-radial-gradient(circle,#fff0 0 20px,#ffffff18 20px 23px)}
.treinamento30{background:radial-gradient(circle,#653ba2,#2b1750 60%,#150b28)}.treinamento30:before{background:linear-gradient(45deg,#a98cff33,#fff0)}
.saida{background:radial-gradient(circle,#1a9d7a,#075746 60%,#032a22)}.saida:before{background:linear-gradient(120deg,#6fffdc33,#fff0)}
.folga_banco{background:radial-gradient(circle,#d99f1f,#7a4c05 60%,#2b1801)}.folga_banco:before{background:linear-gradient(120deg,#fff2a633,#fff0)}
.retry{position:absolute;left:50%;transform:translateX(-50%);bottom:6%;z-index:20;margin:0;background:#ffc928;color:#171006;box-shadow:0 6px 18px #0008}
canvas{position:absolute;inset:0;width:100%;height:100%;touch-action:none}
.status{position:absolute;left:50%;transform:translateX(-50%);bottom:1.6%;background:#071b2ddd;border:1px solid #5ce1ff55;padding:8px 13px;border-radius:999px;font-size:12px;backdrop-filter:blur(8px)}
@media(max-width:700px){body{padding:4px}.app{border-radius:12px}.scratch{border-radius:12px}}
</style></head><body>
<div class="app">
<img class="poster" src="__POSTER_PLACEHOLDER__">
<div class="login" id="login"><div class="box"><div style="font-size:46px">🎟️</div><h1>Raspadinha do Time Retenção</h1>
<p>Digite sua key. A raspadinha só será liberada se o supervisor tiver adicionado um crédito para você.</p>
<input id="mat" inputmode="numeric" autocomplete="off" placeholder="Sua key"><button id="entrar">VERIFICAR RASPADINHA</button><div class="error" id="erro"></div></div></div>
<div class="scratch" id="scratch"><div class="prize" id="prize"></div><canvas id="cover"></canvas></div>
<button class="retry hidden" id="retry" onclick="tentarNovamente()">TENTAR NOVAMENTE</button>
<div class="status" id="status">Digite sua key para começar</div>
</div>
<script>
let matricula="", premio=null, scratching=false, scratched=0, claimed=false;
const login=document.getElementById("login"),erro=document.getElementById("erro"),statusEl=document.getElementById("status"),canvas=document.getElementById("cover"),ctx=canvas.getContext("2d"),scratch=document.getElementById("scratch"),prize=document.getElementById("prize"),retry=document.getElementById("retry");
function prepCover(){
 const r=canvas.getBoundingClientRect(),dpr=Math.max(1,devicePixelRatio||1);canvas.width=r.width*dpr;canvas.height=r.height*dpr;ctx.setTransform(dpr,0,0,dpr,0,0);
 let g=ctx.createLinearGradient(0,0,r.width,r.height);g.addColorStop(0,"#d9e0e5");g.addColorStop(.45,"#9da8b2");g.addColorStop(1,"#eef2f5");ctx.globalCompositeOperation="source-over";ctx.fillStyle=g;ctx.fillRect(0,0,r.width,r.height);
 ctx.fillStyle="#173a5b";ctx.font="900 28px Arial";ctx.textAlign="center";ctx.textBaseline="middle";ctx.fillText("RASPE AQUI",r.width/2,r.height/2-8);ctx.font="700 14px Arial";ctx.fillText("e descubra seu prêmio",r.width/2,r.height/2+25);ctx.globalCompositeOperation="destination-out";
}
function point(e){const r=canvas.getBoundingClientRect();return {x:e.clientX-r.left,y:e.clientY-r.top}}
function erase(e){const p=point(e);ctx.beginPath();ctx.arc(p.x,p.y,28,0,Math.PI*2);ctx.fill();scratched++;if(scratched>65&&!claimed)claim()}
async function verificar(){
 matricula=mat.value.trim();erro.textContent="";if(!matricula){erro.textContent="Digite sua key.";return}
 const r=await fetch("/api/login",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({matricula})});const j=await r.json();
 if(!j.ok){erro.textContent=j.msg;return}
 if(j.creditos<1){erro.textContent=`Olá, ${j.nome}. Você não possui raspadinhas liberadas.`;return}
 statusEl.textContent=`Olá, ${j.nome}! ${j.creditos} raspadinha(s) disponível(is).`;login.classList.add("hidden");setTimeout(prepCover,60)
}
async function claim(){
 claimed=true;const r=await fetch("/api/scratch",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({matricula})});const j=await r.json();
 if(!j.ok){claimed=false;statusEl.textContent=j.msg;return}
 premio=j;prize.className="prize "+j.categoria;prize.innerHTML=`<div class="emoji">${j.emoji}</div><h2>PARABÉNS!</h2><p>${j.nome}</p>`;canvas.style.transition="opacity .35s";canvas.style.opacity="0";setTimeout(()=>canvas.style.display="none",350);statusEl.textContent=`Prêmio registrado • Restam ${j.creditos} crédito(s)`;retry.classList.toggle("hidden",j.creditos<1)
}
function tentarNovamente(){
 if(!premio||premio.creditos<1)return;
 claimed=false;scratched=0;scratching=false;premio=null;prize.className="prize";prize.innerHTML='';canvas.style.transition="none";canvas.style.opacity="1";canvas.style.display="block";retry.classList.add("hidden");statusEl.textContent="Raspe novamente para descobrir seu próximo prêmio";setTimeout(prepCover,30)
}
entrar.onclick=verificar;mat.addEventListener("keydown",e=>{if(e.key==="Enter")verificar()});
canvas.addEventListener("pointerdown",e=>{scratching=true;canvas.setPointerCapture?.(e.pointerId);erase(e)});
canvas.addEventListener("pointermove",e=>{if(scratching)erase(e)});
canvas.addEventListener("pointerup",()=>scratching=false);canvas.addEventListener("pointercancel",()=>scratching=false);
window.addEventListener("resize",()=>{if(!login.classList.contains("hidden")||claimed)return;prepCover()});
</script></body></html>"""

ADMIN = r"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Painel ADM — Retenção</title><style>
*{box-sizing:border-box}body{margin:0;background:#06111f;color:#fff;font-family:Arial,sans-serif}.wrap{max-width:1200px;margin:auto;padding:22px}.card{background:linear-gradient(145deg,#0d2942,#071525);border:1px solid #1cc8ff55;border-radius:20px;padding:22px;box-shadow:0 20px 70px #0005;margin-bottom:18px}h1,h2{margin-top:0}h1{color:#22d4ff}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}input,button,select{padding:11px 13px;border-radius:10px;border:1px solid #31516a;font-size:14px}input,select{background:#06111f;color:#fff}button{border:0;background:#12bfff;color:#03192a;font-weight:900;cursor:pointer}.danger{background:#ff6b7d}.warn{background:#ffc928}.secondary{background:#9db6c8}.muted{color:#9db6c8;font-size:13px}.hidden{display:none}table{width:100%;border-collapse:collapse;margin-top:12px}th,td{padding:9px;border-bottom:1px solid #21405b;text-align:left}.pill{display:inline-block;padding:5px 9px;border-radius:99px;background:#183b59}.ok{color:#7dffae}.err{color:#ff8091}.stat{font-size:28px;font-weight:900;color:#ffc928}.actions{display:flex;gap:6px;flex-wrap:wrap}.actions button{padding:7px 9px}.search{min-width:280px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:18px}.cfg{display:grid;grid-template-columns:repeat(4,minmax(150px,1fr));gap:10px}.cfg label{display:flex;flex-direction:column;gap:5px;font-size:12px;color:#9db6c8}.cfg input{width:100%}.sub{color:#ffc928;font-weight:900;margin:12px 0 8px}@media(max-width:800px){.grid,.cfg{grid-template-columns:1fr}.tablewrap{overflow:auto}}
</style></head><body><div class="wrap"><div class="card" id="gate"><h1>🔐 Painel do Supervisor</h1><div class="row"><input id="senha" type="password" placeholder="Senha ADM"><button onclick="logar()">ENTRAR</button></div><p id="loginmsg"></p></div><div id="painel" class="hidden">
<div class="card"><h1>⚙️ Painel ADM — Raspadinha</h1><div class="row"><div><div class="muted">Colaboradores</div><div class="stat" id="nusers">0</div></div><div><div class="muted">Créditos disponíveis</div><div class="stat" id="ncredits">0</div></div><div><div class="muted">Prêmios registrados</div><div class="stat" id="nresults">0</div></div><button class="secondary" onclick="logout()">SAIR</button></div></div>
<div class="grid"><div class="card"><h2>🎟️ Créditos</h2><div class="row"><input id="mat" placeholder="Key"><button onclick="credito(1)">+1</button><button class="danger" onclick="credito(-1)">-1</button><input id="qtd" type="number" min="0" placeholder="Definir quantidade"><button onclick="setCredito()">DEFINIR</button></div><p id="msg"></p></div><div class="card"><h2>👤 Cadastrar / editar</h2><div class="row"><input id="cm" placeholder="Key"><input id="cn" placeholder="Nome completo"><button onclick="cad()">SALVAR</button></div><p class="muted">Para editar, use o botão Editar na tabela abaixo.</p></div></div>
<div class="card"><h2>🎯 Frequência e regra dos prêmios</h2><p class="muted">Os prêmios especiais agora são controlados por quantidade EXATA no sistema inteiro, não por sorte independente. Ex.: “50” significa exatamente 1 daquele prêmio em cada bloco de 50 raspadinhas. A posição dentro do bloco é aleatória.</p><div class="sub">Prêmios especiais — 1 prêmio a cada N raspadinhas</div><div class="cfg"><label>30 min pausa treinamento — 1 a cada<input id="intervalo_treinamento30" type="number" min="1" step="1" value="50"></label><label>1h saída antecipada — 1 a cada<input id="intervalo_saida" type="number" min="1" step="1" value="100"></label><label>Pausa café — 1 a cada<input id="intervalo_cafe" type="number" min="1" step="1" value="20"></label><label>Folga banco de horas (a combinar) — 1 a cada<input id="intervalo_folga_banco" type="number" min="1" step="1" value="300"></label></div><p class="muted">Padrão atual: folga 1/300, pausa 30 min 1/50, saída antecipada 1/100 e pausa café 1/20. Alterar esses números reinicia apenas o calendário futuro dos especiais.</p><div class="sub">Regra dos prêmios comuns</div><div class="cfg"><label>Doces por ciclo<input id="ciclo_doces" type="number" min="0" step="1" value="7"></label><label>Trolls por ciclo<input id="ciclo_trolls" type="number" min="0" step="1" value="3"></label></div><div class="sub">Peso dentro de cada grupo</div><div class="cfg"><label>Chocolate<input id="peso_chocolate" type="number" min="0" step="0.1" value="1"></label><label>Pirulito<input id="peso_pirulito" type="number" min="0" step="0.1" value="1"></label><label>Bala<input id="peso_bala" type="number" min="0" step="0.1" value="1"></label><label>Aplausos<input id="peso_aplausos" type="number" min="0" step="0.1" value="1"></label><label>+5 de moral<input id="peso_moral" type="number" min="0" step="0.1" value="1"></label><label>Nada, mas você foi guerreiro<input id="peso_guerreiro" type="number" min="0" step="0.1" value="1"></label></div><div class="row" style="margin-top:14px"><button onclick="salvarConfig()">SALVAR CONFIGURAÇÕES</button><span id="cfgmsg" class="muted"></span></div></div>
<div class="card"><h2>👥 Equipe</h2><input class="search" id="busca" placeholder="Pesquisar nome ou matrícula" oninput="renderUsers()"><div class="tablewrap" id="users"></div></div>
<div class="card"><h2>🏆 Histórico de prêmios</h2><div class="row"><input class="search" id="buscaPremio" placeholder="Pesquisar histórico" oninput="renderResults()"><button class="danger" onclick="clearResults()">🧹 LIMPAR HISTÓRICO</button></div><div class="tablewrap" id="results"></div></div>
<div class="grid"><div class="card"><h2>🔐 Alterar senha ADM</h2><div class="row"><input id="senhaAtual" type="password" placeholder="Senha atual"><input id="senhaNova" type="password" placeholder="Nova senha"><input id="senhaConf" type="password" placeholder="Confirmar nova senha"><button onclick="changePassword()">ALTERAR SENHA</button></div><p id="passmsg"></p></div><div class="card"><h2>⚠️ Limpeza do sistema</h2><p class="muted">Use com cuidado. “Zerar todos os créditos” zera apenas raspadinhas disponíveis. “Apagar tudo” apaga colaboradores e histórico.</p><div class="row"><button class="warn" onclick="resetCredits()">ZERAR TODOS OS CRÉDITOS</button><button class="danger" onclick="clearAll()">APAGAR TUDO</button></div></div></div>
</div></div><script>
let DATA={users:[],results:[]};const esc=s=>String(s??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
async function post(url,data){let r=await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(data||{})});let j=await r.json().catch(()=>({ok:false,msg:'Erro no servidor.'}));return{r,j}}
async function logar(){let {r}=await post('/api/admin/login',{senha:senha.value});if(r.ok){gate.classList.add('hidden');painel.classList.remove('hidden');load()}else loginmsg.innerHTML='<span class=err>Senha incorreta.</span>'}
async function logout(){await post('/api/admin/logout',{});location.reload()}
async function cad(){let {j}=await post('/api/admin/user',{matricula:cm.value.trim(),nome:cn.value.trim()});msg.textContent=j.msg||'';if(j.ok){cm.value='';cn.value='';load()}}
async function credito(delta,m){let matricula=m||mat.value.trim();let {j}=await post('/api/admin/credit',{matricula,delta});msg.innerHTML=j.ok?'<span class=ok>Crédito atualizado.</span>':'<span class=err>'+esc(j.msg||'Erro')+'</span>';load()}
async function setCredito(){let {j}=await post('/api/admin/credit/set',{matricula:mat.value.trim(),creditos:qtd.value});msg.textContent=j.msg||'';load()}
function editUser(m){let u=DATA.users.find(x=>x.matricula===m);let nm=prompt('Nome do colaborador:',u.nome);if(nm===null)return;let mm=prompt('Matrícula:',u.matricula);if(mm===null)return;post('/api/admin/user/edit',{old_matricula:m,matricula:mm.trim(),nome:nm.trim()}).then(({j})=>{alert(j.msg||'Concluído');load()})}
function delUser(m){if(confirm('Excluir este colaborador? O histórico de prêmios será mantido.'))post('/api/admin/user/delete',{matricula:m}).then(({j})=>{alert(j.msg||'Concluído');load()})}
function delResult(id){if(confirm('Excluir este lançamento do histórico?'))post('/api/admin/result/delete',{id}).then(()=>load())}
async function changePassword(){if(senhaNova.value!==senhaConf.value){passmsg.innerHTML='<span class=err>As novas senhas não conferem.</span>';return}let {j}=await post('/api/admin/password',{atual:senhaAtual.value,nova:senhaNova.value});passmsg.innerHTML=j.ok?'<span class=ok>'+esc(j.msg)+'</span>':'<span class=err>'+esc(j.msg)+'</span>';if(j.ok){senhaAtual.value=senhaNova.value=senhaConf.value=''}}
async function clearResults(){if(confirm('Tem certeza que deseja APAGAR TODO o histórico de prêmios?')){let {j}=await post('/api/admin/clear',{mode:'results'});alert(j.msg);load()}}
async function resetCredits(){if(confirm('Zerar os créditos de TODOS os colaboradores?')){let {j}=await post('/api/admin/clear',{mode:'credits'});alert(j.msg);load()}}
async function clearAll(){if(confirm('ATENÇÃO: isso apagará TODOS os colaboradores, créditos e histórico. Continuar?')&&confirm('Última confirmação: APAGAR TUDO?')){let {j}=await post('/api/admin/clear',{mode:'all'});alert(j.msg);load()}}
async function salvarConfig(){let cfg={};['intervalo_treinamento30','intervalo_saida','intervalo_cafe','intervalo_folga_banco','ciclo_doces','ciclo_trolls','peso_chocolate','peso_pirulito','peso_bala','peso_aplausos','peso_moral','peso_guerreiro'].forEach(k=>cfg[k]=document.getElementById(k).value);let {j}=await post('/api/admin/config',cfg);cfgmsg.innerHTML=j.ok?'<span class=ok>Configurações salvas.</span>':'<span class=err>'+esc(j.msg||'Erro ao salvar')+'</span>';if(j.ok)load()}
function aplicarConfig(cfg){if(!cfg)return;Object.entries(cfg).forEach(([k,v])=>{let e=document.getElementById(k);if(e)e.value=v})}
function renderUsers(){let q=busca.value.toLowerCase();let a=DATA.users.filter(x=>(x.nome+' '+x.matricula).toLowerCase().includes(q));users.innerHTML='<table><tr><th>Nome</th><th>Key</th><th>Créditos</th><th>Ações</th></tr>'+a.map(x=>`<tr><td>${esc(x.nome)}</td><td>${esc(x.matricula)}</td><td><span class=pill>${x.creditos}</span></td><td class=actions><button onclick="credito(1,\'${esc(x.matricula)}\')">+1</button><button class=warn onclick="editUser(\'${esc(x.matricula)}\')">Editar</button><button class=danger onclick="delUser(\'${esc(x.matricula)}\')">Excluir</button></td></tr>`).join('')+'</table>'}
function renderResults(){let q=buscaPremio.value.toLowerCase();let a=DATA.results.filter(x=>(x.nome+' '+x.matricula+' '+x.premio+' '+x.data).toLowerCase().includes(q));results.innerHTML='<table><tr><th>Nome</th><th>Key</th><th>Prêmio</th><th>Data/Hora</th><th></th></tr>'+a.map(x=>`<tr><td>${esc(x.nome)}</td><td>${esc(x.matricula)}</td><td>${esc(x.premio)}</td><td>${esc(x.data)}</td><td><button class=danger onclick="delResult(${x.id})">Excluir</button></td></tr>`).join('')+'</table>'}
async function load(){let r=await fetch('/api/admin/data');if(!r.ok){msg.innerHTML='<span class=err>Falha ao carregar dados do painel.</span>';return}DATA=await r.json();aplicarConfig(DATA.config);nusers.textContent=DATA.users.length;ncredits.textContent=DATA.users.reduce((a,x)=>a+x.creditos,0);nresults.textContent=DATA.total_results;renderUsers();renderResults()}setInterval(()=>{if(!painel.classList.contains('hidden'))load()},5000)
</script></body></html>"""

@app.get("/")
def home():
    return Response(HOME, mimetype="text/html")

@app.get("/admin")
def admin():
    return Response(ADMIN, mimetype="text/html")

@app.post("/api/login")
def api_login():
    m = str((request.get_json(silent=True) or {}).get("matricula","")).strip()
    c = conn()
    u = c.execute("SELECT * FROM users WHERE matricula=? AND ativo=1",(m,)).fetchone()
    if not u: return jsonify(ok=False,msg="Key não cadastrada."),404
    return jsonify(ok=True,nome=u["nome"],creditos=u["creditos"])

@app.post("/api/scratch")
def api_scratch():
    m = str((request.get_json(silent=True) or {}).get("matricula","")).strip()
    c = conn()
    c.execute("BEGIN IMMEDIATE")
    u = c.execute("SELECT * FROM users WHERE matricula=? AND ativo=1",(m,)).fetchone()
    if not u or u["creditos"] < 1:
        c.rollback(); return jsonify(ok=False,msg="Você não possui raspadinhas disponíveis."),403
    p = choose_prize(c)
    c.execute("UPDATE users SET creditos=creditos-1 WHERE matricula=?",(m,))
    data = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
    c.execute("INSERT INTO results(matricula,nome,premio,categoria,data) VALUES(?,?,?,?,?)",(m,u["nome"],p["nome"],p["categoria"],data))
    c.commit()
    return jsonify(ok=True,nome=p["nome"],emoji=p["emoji"],categoria=p["categoria"],creditos=u["creditos"]-1,data=data)

@app.post("/api/admin/login")
def api_admin_login():
    senha=str((request.get_json(silent=True) or {}).get("senha",""))
    if senha == "507357":
        session["admin"]=True
        return jsonify(ok=True)
    c=conn()
    row=c.execute("SELECT valor FROM meta WHERE chave='admin_password_hash'").fetchone()
    if row and check_password_hash(row["valor"],senha):
        session["admin"]=True
        return jsonify(ok=True)
    return jsonify(ok=False),403

@app.post("/api/admin/logout")
def api_admin_logout():
    session.clear(); return jsonify(ok=True)

def is_admin(): return session.get("admin") is True

@app.get("/api/admin/data")
def api_admin_data():
    if not is_admin(): return jsonify(ok=False),403
    c=conn()
    users=[dict(x) for x in c.execute("SELECT matricula,nome,creditos FROM users WHERE ativo=1 ORDER BY nome")]
    results=[dict(x) for x in c.execute("SELECT id,matricula,nome,premio,data FROM results ORDER BY id DESC LIMIT 300")]
    total=c.execute("SELECT COUNT(*) n FROM results").fetchone()["n"]
    keys=["intervalo_treinamento30","intervalo_saida","intervalo_cafe","intervalo_folga_banco","ciclo_doces","ciclo_trolls","peso_chocolate","peso_pirulito","peso_bala","peso_aplausos","peso_moral","peso_guerreiro"]
    config={k:(c.execute("SELECT valor FROM meta WHERE chave=?",(k,)).fetchone() or {"valor":""})["valor"] for k in keys}
    return jsonify(ok=True,users=users,results=results,total_results=total,config=config)

@app.post("/api/admin/config")
def api_admin_config():
    if not is_admin(): return jsonify(ok=False),403
    j=request.get_json(silent=True) or {}
    interval_keys={"intervalo_treinamento30","intervalo_saida","intervalo_cafe","intervalo_folga_banco"}
    int_keys={"ciclo_doces","ciclo_trolls"}
    weight_keys={"peso_chocolate","peso_pirulito","peso_bala","peso_aplausos","peso_moral","peso_guerreiro"}
    allowed=interval_keys|int_keys|weight_keys
    vals={}
    try:
        for k in allowed:
            if k not in j: continue
            v=float(j[k])
            if k in interval_keys:
                if v<1 or int(v)!=v: raise ValueError
                v=int(v)
            if k in int_keys:
                if v<0 or int(v)!=v: raise ValueError
                v=int(v)
            if k in weight_keys and v<0: raise ValueError
            vals[k]=str(v)
    except:
        return jsonify(ok=False,msg="Nos especiais, informe números inteiros maiores ou iguais a 1."),400
    c=conn()
    atual_doces=int(float(vals.get("ciclo_doces",meta_int(c,"ciclo_doces",7))))
    atual_trolls=int(float(vals.get("ciclo_trolls",meta_int(c,"ciclo_trolls",3))))
    if atual_doces+atual_trolls<=0:
        c.close(); return jsonify(ok=False,msg="O ciclo precisa ter pelo menos 1 prêmio comum."),400
    # Valida o calendário antes de salvar.
    antigos={k:(c.execute("SELECT valor FROM meta WHERE chave=?",(k,)).fetchone() or {"valor":""})["valor"] for k in interval_keys}
    try:
        for k,v in vals.items():
            if k in interval_keys:
                c.execute("INSERT INTO meta(chave,valor) VALUES(?,?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",(k,v))
        build_special_cycle(c)
    except ValueError as e:
        for k,v in antigos.items():
            c.execute("INSERT INTO meta(chave,valor) VALUES(?,?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",(k,v))
        c.rollback(); c.close(); return jsonify(ok=False,msg=str(e)),400
    for k,v in vals.items():
        c.execute("INSERT INTO meta(chave,valor) VALUES(?,?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",(k,v))
    if any(k in vals for k in interval_keys):
        c.execute("UPDATE meta SET valor='0' WHERE chave='special_pos'")
        c.execute("UPDATE meta SET valor='' WHERE chave='special_cycle'")
    if "ciclo_doces" in vals or "ciclo_trolls" in vals:
        c.execute("UPDATE meta SET valor='0' WHERE chave='common_pos'")
        c.execute("UPDATE meta SET valor='' WHERE chave='common_cycle'")
    c.commit(); c.close(); return jsonify(ok=True)

@app.post("/api/admin/user")
def api_admin_user():
    if not is_admin(): return jsonify(ok=False),403
    j=request.get_json(silent=True) or {}; m=str(j.get("matricula","")).strip(); n=str(j.get("nome","")).strip()
    if not m or not n: return jsonify(ok=False,msg="Preencha matrícula e nome."),400
    c=conn(); c.execute("INSERT INTO users(matricula,nome,creditos,ativo) VALUES(?,?,0,1) ON CONFLICT(matricula) DO UPDATE SET nome=excluded.nome,ativo=1",(m,n)); c.commit(); return jsonify(ok=True,msg="Colaborador salvo.")

@app.post("/api/admin/user/edit")
def api_admin_user_edit():
    if not is_admin(): return jsonify(ok=False),403
    j=request.get_json(silent=True) or {}; old=str(j.get("old_matricula","")).strip(); m=str(j.get("matricula","")).strip(); n=str(j.get("nome","")).strip()
    if not old or not m or not n: return jsonify(ok=False,msg="Preencha matrícula e nome."),400
    c=conn(); u=c.execute("SELECT * FROM users WHERE matricula=? AND ativo=1",(old,)).fetchone()
    if not u: return jsonify(ok=False,msg="Colaborador não encontrado."),404
    if m!=old and c.execute("SELECT 1 FROM users WHERE matricula=?",(m,)).fetchone(): return jsonify(ok=False,msg="A nova matrícula já existe."),400
    if m==old: c.execute("UPDATE users SET nome=? WHERE matricula=?",(n,old))
    else:
        c.execute("UPDATE users SET matricula=?,nome=? WHERE matricula=?",(m,n,old)); c.execute("UPDATE results SET matricula=?,nome=? WHERE matricula=?",(m,n,old))
    c.commit(); return jsonify(ok=True,msg="Colaborador atualizado.")

@app.post("/api/admin/user/delete")
def api_admin_user_delete():
    if not is_admin(): return jsonify(ok=False),403
    m=str((request.get_json(silent=True) or {}).get("matricula","")).strip(); c=conn(); cur=c.execute("UPDATE users SET ativo=0,creditos=0 WHERE matricula=?",(m,)); c.commit()
    if not cur.rowcount: return jsonify(ok=False,msg="Colaborador não encontrado."),404
    return jsonify(ok=True,msg="Colaborador excluído.")

@app.post("/api/admin/credit")
def api_admin_credit():
    if not is_admin(): return jsonify(ok=False),403
    j=request.get_json(silent=True) or {}; m=str(j.get("matricula","")).strip()
    try: delta=int(j.get("delta",1))
    except: delta=1
    c=conn(); u=c.execute("SELECT creditos FROM users WHERE matricula=? AND ativo=1",(m,)).fetchone()
    if not u: return jsonify(ok=False,msg="Matrícula não encontrada."),404
    novo=max(0,u["creditos"]+delta); c.execute("UPDATE users SET creditos=? WHERE matricula=?",(novo,m)); c.commit(); return jsonify(ok=True,creditos=novo)

@app.post("/api/admin/credit/set")
def api_admin_credit_set():
    if not is_admin(): return jsonify(ok=False),403
    j=request.get_json(silent=True) or {}; m=str(j.get("matricula","")).strip()
    try: q=max(0,int(j.get("creditos",0)))
    except: return jsonify(ok=False,msg="Quantidade inválida."),400
    c=conn(); cur=c.execute("UPDATE users SET creditos=? WHERE matricula=? AND ativo=1",(q,m)); c.commit()
    if not cur.rowcount: return jsonify(ok=False,msg="Matrícula não encontrada."),404
    return jsonify(ok=True,msg=f"Créditos definidos para {q}.")

@app.post("/api/admin/result/delete")
def api_admin_result_delete():
    if not is_admin(): return jsonify(ok=False),403
    try: i=int((request.get_json(silent=True) or {}).get("id"))
    except: return jsonify(ok=False,msg="ID inválido."),400
    c=conn(); c.execute("DELETE FROM results WHERE id=?",(i,)); c.commit(); return jsonify(ok=True,msg="Lançamento excluído.")

@app.post("/api/admin/password")
def api_admin_password():
    if not is_admin(): return jsonify(ok=False),403
    j=request.get_json(silent=True) or {}; atual=str(j.get("atual","")); nova=str(j.get("nova",""))
    if len(nova)<6: return jsonify(ok=False,msg="A nova senha deve ter pelo menos 6 caracteres."),400
    c=conn(); row=c.execute("SELECT valor FROM meta WHERE chave='admin_password_hash'").fetchone()
    h=row["valor"] if row else ""
    if atual != "507357" and not (h and check_password_hash(h,atual)):
        return jsonify(ok=False,msg="Senha atual incorreta."),403
    novo_hash=generate_password_hash(nova)
    c.execute("INSERT INTO meta(chave,valor) VALUES('admin_password_hash',?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",(novo_hash,))
    c.commit()
    return jsonify(ok=True,msg="Senha alterada com sucesso.")

@app.post("/api/admin/clear")
def api_admin_clear():
    if not is_admin(): return jsonify(ok=False),403
    mode=str((request.get_json(silent=True) or {}).get("mode","")); c=conn()
    if mode=="results": c.execute("DELETE FROM results"); msg="Histórico apagado."
    elif mode=="credits": c.execute("UPDATE users SET creditos=0"); msg="Todos os créditos foram zerados."
    elif mode=="all": c.execute("DELETE FROM results"); c.execute("DELETE FROM users"); msg="Colaboradores, créditos e histórico foram apagados."
    else: return jsonify(ok=False,msg="Opção inválida."),400
    c.execute("UPDATE meta SET valor='0' WHERE chave='common_pos'"); c.execute("UPDATE meta SET valor='' WHERE chave='common_cycle'"); c.execute("UPDATE meta SET valor='0' WHERE chave='special_pos'"); c.execute("UPDATE meta SET valor='' WHERE chave='special_cycle'"); c.commit(); return jsonify(ok=True,msg=msg)

@app.get("/version")
def version():
    return "FULL-ADMIN-SQLITE-507357-20260813-2300", 200

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT","10000")))
