using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Reflection;
using System.Text;
using UnityEngine;

/// <summary>
/// Runtime-логгер Kingdom: Classic (Steam 368230), вклеен в Assembly-CSharp.dll
/// (патч через Mono.Cecil). Пишет состояние партии в <Game>_Data/kcdbg.txt
/// и редкие события "KCEVT|..." в лог Unity (output_log.txt).
///
/// v5 - исправление падения v4:
///   В .NET 3.5 (на котором работает Unity 5.3) у System.Type НЕТ оператора
///   == / != (метод System.Type.op_Equality появился только в .NET 4.0).
///   Любое сравнение Type с null компилировалось в вызов Type.op_Equality и
///   падало с MissingMethodException каждый кадр -> лог не писался.
///   Теперь сравнение через ReferenceEquals (object), без перегрузок операторов.
///
/// v5 - новые поля:
///   |ps:  состояние оплаты короля (None/Holding/Transaction/Completed/Cancelling)
///   |as:  состояние действия короля (Walk/Run/Stand/Eat/Rear) - нужно для налогов
///   |pt:  таймер оплаты
///   sel:  теперь с именем GameObject выбранного Payable
///   Каждая секция обёрнута в try/catch: одна ошибка не роняет весь лог.
/// </summary>
public static class KCDbg
{
    const BindingFlags BF = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

    static float _acc;
    static string _path;
    static readonly StringBuilder SB = new StringBuilder();

    static Type T_player, T_director, T_wallet, T_kingdom, T_enemies, T_portal,
                T_damageable, T_camp, T_shop, T_farm, T_statue, T_coin, T_troll, T_ogre, T_squid,
                T_guard, T_payable, T_archer, T_worker, T_farmer, T_knight, T_tool, T_tech, T_wall,
                T_enemy, T_peasant, T_beggar, T_boss;

    static int _prevDay = -1;
    static int _prevEnemies = -1;
    static int _prevPurse = -1;
    static int _prevArchers = -1;

    // Безопасное сравнение с null: НЕ используем == на Type (нет op_Equality в .NET 3.5)
    static bool IsNull(object o) { return ReferenceEquals(o, null); }

    static Type T(string name)
    {
        try { return Type.GetType(name + ", Assembly-CSharp"); }
        catch (Exception) { return null; }
    }

    static void InitTypes()
    {
        if (!IsNull(T_player)) return;
        T_player = T("Player");
        T_director = T("Director");
        T_wallet = T("Wallet");
        T_kingdom = T("Kingdom");
        T_enemies = T("EnemyManager");
        T_portal = T("Portal");
        T_damageable = T("Damageable");
        T_camp = T("BeggarCamp");
        T_shop = T("PayableShop");
        T_farm = T("Farmland");
        T_statue = T("Statue");
        T_coin = T("Coin");
        T_troll = T("Troll");
        T_ogre = T("Ogre");
        T_squid = T("Squid");
        T_guard = T("GuardSlot");
        T_payable = T("Payable");
        T_archer = T("Archer");
        T_worker = T("Worker");
        T_farmer = T("Farmer");
        T_knight = T("Knight");
        T_tool = T("DroppableTool");
        T_tech = T("TechUpgrades");
        T_wall = T("Wall");
        T_enemy = T("Enemy");
        T_peasant = T("Peasant");
        T_beggar = T("Beggar");
        T_boss = T("Boss");
    }

    static object GetField(object o, string name)
    {
        if (IsNull(o)) return null;
        try
        {
            FieldInfo fi = o.GetType().GetField(name, BF);
            if (IsNull(fi)) return null;
            return fi.GetValue(o);
        }
        catch (Exception) { return null; }
    }

    static object GetProp(object o, string name)
    {
        if (IsNull(o)) return null;
        try
        {
            PropertyInfo pi = o.GetType().GetProperty(name, BF);
            if (!IsNull(pi)) return pi.GetValue(o, null);
            FieldInfo fi = o.GetType().GetField(name, BF);
            if (!IsNull(fi)) return fi.GetValue(o);
        }
        catch (Exception) { }
        return null;
    }

    static int CountAny(object o)
    {
        if (IsNull(o)) return -1;
        try
        {
            PropertyInfo pi = o.GetType().GetProperty("Count");
            if (!IsNull(pi)) return (int)pi.GetValue(o, null);
            ICollection ci = o as ICollection;
            if (!IsNull(ci)) return ci.Count;
        }
        catch (Exception) { }
        return -1;
    }

    static float X(object o)
    {
        try
        {
            Component c = o as Component;
            if (IsNull(c)) return float.NaN;
            return c.transform.position.x;
        }
        catch (Exception) { return float.NaN; }
    }

    static float Y(object o)
    {
        try
        {
            Component c = o as Component;
            if (IsNull(c)) return float.NaN;
            return c.transform.position.y;
        }
        catch (Exception) { return float.NaN; }
    }

    static string GoName(object o)
    {
        try
        {
            Component c = o as Component;
            if (IsNull(c)) return "-";
            return c.gameObject.name;
        }
        catch (Exception) { return "-"; }
    }

    static string S(object v)
    {
        if (IsNull(v)) return "-";
        try
        {
            if (v is float) return ((float)v).ToString("F2");
            return v.ToString();
        }
        catch (Exception) { return "?"; }
    }

    static void Err(Exception e)
    {
        try { SB.Append("!E:").Append(e.GetType().Name).Append(","); }
        catch (Exception) { }
    }

    static void Main()
    {
        InitTypes();
        if (IsNull(T_player)) return;

        SB.Length = 0;
        SB.Append("KC|");

        UnityEngine.Object[] players = UnityEngine.Object.FindObjectsOfType(T_player);
        object player = null;
        for (int i = 0; i < players.Length; i++) { player = players[i]; break; }
        if (IsNull(player)) return;

        float px = X(player);
        SB.Append("px=").Append(px.ToString("F2"));
        SB.Append(",py=").Append(Y(player).ToString("F2"));
        SB.Append(",st=").Append(S(GetProp(player, "stamina")));

        // ---- состояние оплаты и действия короля
        try
        {
            SB.Append("|ps:").Append(S(GetField(player, "_payState")));
            SB.Append("|as:").Append(S(GetField(player, "_actionState")));
            object pt = GetField(player, "_payTimer");
            if (!IsNull(pt)) SB.Append("|pt:").Append(S(pt));
        }
        catch (Exception e) { Err(e); }

        try
        {
            object dmg = GetField(player, "_damageable");
            if (!IsNull(dmg))
                SB.Append(",hp=").Append(S(GetProp(dmg, "hitPoints")));
        }
        catch (Exception e) { Err(e); }

        int purse = -1;
        try
        {
            object wallet = GetField(player, "_wallet");
            if (IsNull(wallet))
            {
                UnityEngine.Object[] ws = UnityEngine.Object.FindObjectsOfType(T_wallet);
                for (int i = 0; i < ws.Length; i++)
                {
                    object show = GetProp(ws[i], "showCoinBag");
                    if (!IsNull(show) && show.ToString() == "True") { wallet = ws[i]; break; }
                }
            }
            if (!IsNull(wallet)) purse = Convert.ToInt32(GetProp(wallet, "coins"));
        }
        catch (Exception e) { Err(e); }
        SB.Append(",purse=").Append(purse);

        // ---- корона
        int hasCrown = -1;
        try
        {
            object cs = GetField(player, "crownSprite");
            if (!IsNull(cs))
            {
                Component cc = cs as Component;
                if (!IsNull(cc))
                {
                    SpriteRenderer sr = cc as SpriteRenderer;
                    if (!IsNull(sr)) hasCrown = sr.enabled ? 1 : 0;
                }
            }
        }
        catch (Exception e) { Err(e); }
        SB.Append(",hc=").Append(hasCrown);
        SB.Append("|crown:");
        try
        {
            GameObject[] crowns = GameObject.FindGameObjectsWithTag("Crown");
            bool first = true;
            for (int i = 0; i < crowns.Length; i++)
            {
                if (!first) SB.Append(",");
                first = false;
                SB.Append(crowns[i].transform.position.x.ToString("F1"));
            }
            if (first) SB.Append("-");
        }
        catch (Exception e) { SB.Append("-"); Err(e); }

        // ---- выбранный Payable
        SB.Append("|sel:");
        try
        {
            object sel = GetField(player, "_selectedPayable");
            if (!IsNull(sel))
            {
                SB.Append(sel.GetType().Name).Append(",")
                  .Append(Convert.ToInt32(GetProp(sel, "price"))).Append("@")
                  .Append(X(sel).ToString("F1")).Append("~").Append(GoName(sel));
            }
            else SB.Append("-");
        }
        catch (Exception e) { SB.Append("-"); Err(e); }

        int day = -1;
        try
        {
            UnityEngine.Object[] dirs = UnityEngine.Object.FindObjectsOfType(T_director);
            object dir = null;
            if (dirs.Length > 0) dir = dirs[0];
            if (!IsNull(dir))
            {
                day = Convert.ToInt32(GetProp(dir, "currentDay"));
                SB.Append("|day=").Append(day);
                SB.Append(",time=").Append(S(GetProp(dir, "currentTime")));
                SB.Append(",playing=").Append(S(GetProp(dir, "playing")));
                SB.Append(",next=").Append(S(GetProp(dir, "nextEventName")));
            }
        }
        catch (Exception e) { Err(e); }

        int archers = -1, workers = -1, farmers = -1, knights = -1, walls = -1,
            shopsN = -1, farmsN = -1, portalsN = -1, campsN = -1, work = -1;
        try
        {
            UnityEngine.Object[] kms = UnityEngine.Object.FindObjectsOfType(T_kingdom);
            object king = null;
            if (kms.Length > 0) king = kms[0];
            if (!IsNull(king))
            {
                archers = CountAny(GetField(king, "_archers"));
                workers = CountAny(GetField(king, "_workers"));
                farmers = CountAny(GetField(king, "_farmers"));
                knights = CountAny(GetField(king, "_knights"));
                walls = CountAny(GetField(king, "_walls"));
                shopsN = CountAny(GetField(king, "_shops"));
                farmsN = CountAny(GetField(king, "_farmlands"));
                portalsN = CountAny(GetField(king, "_portals"));
                campsN = CountAny(GetField(king, "_beggarCamps"));
                work = CountAny(GetField(king, "_availableWork"));
                SB.Append("|dayT=").Append(S(GetProp(king, "isDaytime")));
                SB.Append("|safe=").Append(S(GetProp(king, "isSafe")));
                SB.Append(",cf=").Append(S(GetProp(king, "campfirePosition")));
            }
        }
        catch (Exception e) { Err(e); }
        SB.Append(",arc=").Append(archers);
        SB.Append(",wrk=").Append(workers);
        SB.Append(",frm=").Append(farmers);
        SB.Append(",knt=").Append(knights);
        SB.Append(",wall=").Append(walls);
        SB.Append(",shop=").Append(shopsN);
        SB.Append(",farm=").Append(farmsN);
        SB.Append(",portal=").Append(portalsN);
        SB.Append(",camp=").Append(campsN);
        SB.Append(",work=").Append(work);

        int enemies = -1;
        try
        {
            UnityEngine.Object[] ems = UnityEngine.Object.FindObjectsOfType(T_enemies);
            object em = null;
            if (ems.Length > 0) em = ems[0];
            if (!IsNull(em))
            {
                enemies = CountAny(GetField(em, "_enemies"));
                SB.Append(",atk=").Append(S(GetProp(em, "enemiesAttacking")));
            }
        }
        catch (Exception e) { Err(e); }
        SB.Append(",en=").Append(enemies);
        try
        {
            if (!IsNull(T_troll)) SB.Append(",troll=").Append(UnityEngine.Object.FindObjectsOfType(T_troll).Length);
            if (!IsNull(T_ogre)) SB.Append(",ogre=").Append(UnityEngine.Object.FindObjectsOfType(T_ogre).Length);
            if (!IsNull(T_squid)) SB.Append(",squid=").Append(UnityEngine.Object.FindObjectsOfType(T_squid).Length);
        }
        catch (Exception e) { Err(e); }

        // ---- враги
        try
        {
            if (!IsNull(T_enemy))
            {
                UnityEngine.Object[] ens = UnityEngine.Object.FindObjectsOfType(T_enemy);
                SB.Append("|enm:");
                for (int i = 0; i < ens.Length; i++)
                {
                    object atk = GetProp(ens[i], "isAttacking");
                    SB.Append(GoName(ens[i])).Append("@").Append(X(ens[i]).ToString("F0"))
                      .Append(":").Append(!IsNull(atk) && atk.ToString() == "True" ? "1" : "0")
                      .Append(",");
                }
            }
        }
        catch (Exception e) { Err(e); }

        // ---- монеты на земле
        try
        {
            if (!IsNull(T_coin))
            {
                UnityEngine.Object[] coins = UnityEngine.Object.FindObjectsOfType(T_coin);
                int nc = 0;
                StringBuilder cb = new StringBuilder();
                for (int i = 0; i < coins.Length; i++)
                {
                    float x = X(coins[i]);
                    if (float.IsNaN(x)) continue;
                    if (Mathf.Abs(x - px) > 40f) continue;
                    nc++;
                    cb.Append(x.ToString("F0")).Append(",");
                }
                SB.Append(",gcoin=").Append(nc);
                SB.Append("|gc:").Append(cb);
            }
        }
        catch (Exception e) { Err(e); }

        // ---- лагеря нищих
        try
        {
            if (!IsNull(T_camp))
            {
                UnityEngine.Object[] cs = UnityEngine.Object.FindObjectsOfType(T_camp);
                SB.Append("|camps:");
                for (int i = 0; i < cs.Length; i++)
                    SB.Append(X(cs[i]).ToString("F0")).Append(",");
            }
        }
        catch (Exception e) { Err(e); }

        // ---- порталы
        try
        {
            if (!IsNull(T_portal))
            {
                UnityEngine.Object[] ps = UnityEngine.Object.FindObjectsOfType(T_portal);
                SB.Append("|ports:");
                for (int i = 0; i < ps.Length; i++)
                {
                    Component pc = ps[i] as Component;
                    object d = (!IsNull(pc) && !IsNull(T_damageable)) ? pc.GetComponent(T_damageable) : null;
                    int hp = !IsNull(d) ? Convert.ToInt32(GetProp(d, "hitPoints")) : -1;
                    object outer = GetProp(ps[i], "outerSpawner");
                    SB.Append(X(ps[i]).ToString("F0")).Append(":").Append(hp)
                      .Append(":").Append(IsNull(outer) ? "?" : (outer.ToString() == "True" ? "1" : "0"))
                      .Append(",");
                }
            }
        }
        catch (Exception e) { Err(e); }

        // ---- статуи
        try
        {
            if (!IsNull(T_statue))
            {
                UnityEngine.Object[] ss = UnityEngine.Object.FindObjectsOfType(T_statue);
                SB.Append("|stat:");
                for (int i = 0; i < ss.Length; i++)
                {
                    string deity = S(GetProp(ss[i], "deity"));
                    float charged = -1f;
                    object cc = GetField(ss[i], "_currentCharge");
                    if (!IsNull(cc)) charged = Convert.ToSingle(cc);
                    SB.Append(deity).Append("@").Append(X(ss[i]).ToString("F0")).Append(":")
                      .Append(charged.ToString("F2")).Append(",");
                }
            }
        }
        catch (Exception e) { Err(e); }

        // ---- башни
        try
        {
            if (!IsNull(T_guard))
            {
                UnityEngine.Object[] gs = UnityEngine.Object.FindObjectsOfType(T_guard);
                SB.Append("|tow:");
                for (int i = 0; i < gs.Length; i++)
                {
                    object avail = GetProp(gs[i], "isAvailable");
                    int free = (!IsNull(avail) && avail.ToString() == "True") ? 1 : 0;
                    SB.Append(X(gs[i]).ToString("F0")).Append(":").Append(free).Append(",");
                }
            }
        }
        catch (Exception e) { Err(e); }

        // ---- граждане
        try
        {
            AppendCitizens(SB, T_archer, "A");
            AppendCitizens(SB, T_worker, "W");
            AppendCitizens(SB, T_farmer, "F");
            AppendCitizens(SB, T_knight, "K");
        }
        catch (Exception e) { Err(e); }

        try
        {
            if (!IsNull(T_peasant))
            {
                UnityEngine.Object[] cs = UnityEngine.Object.FindObjectsOfType(T_peasant);
                for (int i = 0; i < cs.Length; i++)
                    SB.Append("P@").Append(X(cs[i]).ToString("F0")).Append(",");
            }
        }
        catch (Exception e) { Err(e); }

        try
        {
            if (!IsNull(T_beggar))
            {
                UnityEngine.Object[] cs = UnityEngine.Object.FindObjectsOfType(T_beggar);
                for (int i = 0; i < cs.Length; i++)
                    SB.Append("B@").Append(X(cs[i]).ToString("F0")).Append(",");
            }
        }
        catch (Exception e) { Err(e); }

        // ---- рыцари
        try
        {
            if (!IsNull(T_knight))
            {
                UnityEngine.Object[] ks = UnityEngine.Object.FindObjectsOfType(T_knight);
                SB.Append("|kng:");
                for (int i = 0; i < ks.Length; i++)
                {
                    string stt = "?";
                    try
                    {
                        object fsm = GetField(ks[i], "_fsm");
                        object cur = GetProp(fsm, "current");
                        if (!IsNull(cur)) stt = cur.ToString();
                    }
                    catch (Exception) { }
                    object side = GetProp(ks[i], "side");
                    SB.Append(X(ks[i]).ToString("F0")).Append(":").Append(stt)
                      .Append(":").Append(IsNull(side) ? "?" : side.ToString()).Append(",");
                }
            }
        }
        catch (Exception e) { Err(e); }

        // ---- инструменты
        try
        {
            if (!IsNull(T_tool))
            {
                UnityEngine.Object[] ts = UnityEngine.Object.FindObjectsOfType(T_tool);
                SB.Append("|tool:");
                for (int i = 0; i < ts.Length; i++)
                    SB.Append(GoName(ts[i])).Append("@").Append(X(ts[i]).ToString("F0")).Append(",");
            }
        }
        catch (Exception e) { Err(e); }

        // ---- стены
        try
        {
            if (!IsNull(T_wall))
            {
                UnityEngine.Object[] ws2 = UnityEngine.Object.FindObjectsOfType(T_wall);
                SB.Append("|walls:");
                for (int i = 0; i < ws2.Length; i++)
                {
                    Component pc = ws2[i] as Component;
                    object d = (!IsNull(pc) && !IsNull(T_damageable)) ? pc.GetComponent(T_damageable) : null;
                    int hp = !IsNull(d) ? Convert.ToInt32(GetProp(d, "hitPoints")) : -1;
                    object lvl = GetProp(ws2[i], "level");
                    SB.Append(X(ws2[i]).ToString("F0")).Append(":").Append(hp).Append(":")
                      .Append(IsNull(lvl) ? "?" : lvl.ToString()).Append(",");
                }
            }
        }
        catch (Exception e) { Err(e); }

        // ---- фермы
        try
        {
            if (!IsNull(T_farm))
            {
                UnityEngine.Object[] fs = UnityEngine.Object.FindObjectsOfType(T_farm);
                SB.Append("|farms:");
                for (int i = 0; i < fs.Length; i++)
                {
                    object isF = GetField(fs[i], "_isFarmed");
                    SB.Append(X(fs[i]).ToString("F0")).Append(":")
                      .Append(IsNull(isF) ? "?" : isF.ToString()).Append(",");
                }
            }
        }
        catch (Exception e) { Err(e); }

        // ---- лавки
        try
        {
            if (!IsNull(T_shop))
            {
                UnityEngine.Object[] shs = UnityEngine.Object.FindObjectsOfType(T_shop);
                SB.Append("|shops:");
                for (int i = 0; i < shs.Length; i++)
                {
                    object item = GetProp(shs[i], "itemPrefab");
                    string nm = "-";
                    if (!IsNull(item))
                    {
                        Component cc = item as Component;
                        if (!IsNull(cc)) nm = cc.gameObject.name;
                        else nm = item.ToString();
                    }
                    SB.Append(nm).Append("@").Append(X(shs[i]).ToString("F0")).Append(",");
                }
            }
        }
        catch (Exception e) { Err(e); }

        // ---- все Payable
        try
        {
            if (!IsNull(T_payable))
            {
                UnityEngine.Object[] pays = UnityEngine.Object.FindObjectsOfType(T_payable);
                SB.Append("|pay:");
                for (int i = 0; i < pays.Length; i++)
                {
                    float x = X(pays[i]);
                    if (float.IsNaN(x)) continue;
                    SB.Append(pays[i].GetType().Name).Append(",")
                      .Append(Convert.ToInt32(GetProp(pays[i], "price"))).Append("@")
                      .Append(x.ToString("F1"))
                      .Append("~").Append(GoName(pays[i]));
                      object _ap = GetField(pays[i], "_allowPayment");
                      SB.Append("~").Append(_ap != null && (bool)_ap ? "1" : "0")
                      .Append(",");
                }
            }
        }
        catch (Exception e) { Err(e); }

        Write(SB.ToString());

        try
        {
            if (day != _prevDay) { _prevDay = day; Debug.Log("KCEVT|day=" + day); }
            if (purse != _prevPurse) { _prevPurse = purse; Debug.Log("KCEVT|coins=" + purse); }
            if (enemies != _prevEnemies) { _prevEnemies = enemies; Debug.Log("KCEVT|enemies=" + enemies); }
            if (archers != _prevArchers) { _prevArchers = archers; Debug.Log("KCEVT|archers=" + archers); }
        }
        catch (Exception) { }
    }

    static void AppendCitizens(StringBuilder sb, Type type, string tag)
    {
        if (IsNull(type)) return;
        UnityEngine.Object[] cs = UnityEngine.Object.FindObjectsOfType(type);
        for (int i = 0; i < cs.Length; i++)
        {
            object w = GetField(cs[i], "_wallet");
            int coins = 0;
            if (!IsNull(w))
            {
                try { coins = Convert.ToInt32(GetProp(w, "coins")); } catch (Exception) { }
            }
            sb.Append(tag).Append("@").Append(X(cs[i]).ToString("F0")).Append(":").Append(coins).Append(",");
        }
    }

    static void Write(string line)
    {
        try
        {
            if (IsNull(_path))
            {
                _path = Path.Combine(Application.dataPath, "kcdbg.txt");
            }
            File.AppendAllText(_path, line + "\n");
        }
        catch (Exception e)
        {
            Debug.Log("KCERR|" + e.Message);
        }
    }

    public static void Tick()
    {
        try
        {
            _acc += Time.deltaTime;
            if (_acc < 0.25f) return;
            _acc = 0f;
            Main();
        }
        catch (Exception e)
        {
            try { Write("KCERR|" + e.GetType().Name + "|" + e.Message + " @" + e.StackTrace); }
            catch (Exception) { }
            Debug.Log("KCERR|" + e.GetType().Name + "|" + e.Message + " @ " + e.StackTrace);
        }
    }
}
