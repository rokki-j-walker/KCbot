// =============================================================================
// KCLog5.cs - логгер и контроллер скорости для Kingdom: Classic.
//
// ВОССТАНОВЛЕН 2026-11-20 из деплоеного KCLog5.dll (16896 байт, манифест
// KCLog5_new) через ILSpy, потому что исходник в репозитории (631 строка)
// устарел и НЕ содержал speed-логику: deployed-версия читает speed.txt и
// ставит Time.timeScale, а старый исходник - только логирование. Формат
// kcdbg.txt сохранён байт-в-байт (парсер bot3 не меняется).
//
// ИСПРАВЛЕНИЕ (баг пользователя): условие повторного применения скорости.
// Раньше: if (result != _currentSpeed) - применялось ТОЛЬКО при изменении
// ФАЙЛА. Любое внешнее присвоение timeScale (меню игры HideMenu() -> 1f,
// восстановление окна) навсегда оставляло игру на 1x: кэш совпадал с
// файлом, пере-применения не было. Теперь сверяемся с РЕАЛЬНЫМ
// Time.timeScale; 0 (пауза) не трогаем.
//
// Сборка: см. tools/build_kclog5.ps1 (dotnet csc + ссылки из папки игры).
// =============================================================================
using System;
using System.Collections;
using System.IO;
using System.Reflection;
using System.Runtime.CompilerServices;
using System.Text;
using UnityEngine;

[assembly: CompilationRelaxations(8)]
[assembly: RuntimeCompatibility(WrapNonExceptionThrows = true)]
[assembly: AssemblyVersion("0.0.0.0")]
public static class KCDbg
{
	private const BindingFlags BF = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic;

	private static float _acc;

	private static string _path;

	private static readonly StringBuilder SB = new StringBuilder();

	private static float _speedAcc = 0f;

	private static float _currentSpeed = 1f;

	private static bool _speedInitialized = false;

	private static Type T_player;

	private static Type T_director;

	private static Type T_wallet;

	private static Type T_kingdom;

	private static Type T_enemies;

	private static Type T_portal;

	private static Type T_damageable;

	private static Type T_camp;

	private static Type T_shop;

	private static Type T_farm;

	private static Type T_statue;

	private static Type T_coin;

	private static Type T_troll;

	private static Type T_ogre;

	private static Type T_squid;

	private static Type T_guard;

	private static Type T_payable;

	private static Type T_archer;

	private static Type T_worker;

	private static Type T_farmer;

	private static Type T_knight;

	private static Type T_tool;

	private static Type T_tech;

	private static Type T_wall;

	private static Type T_enemy;

	private static Type T_peasant;

	private static Type T_beggar;

	private static Type T_boss;

	private static int _prevDay = -1;

	private static int _prevEnemies = -1;

	private static int _prevPurse = -1;

	private static int _prevArchers = -1;

	private static bool IsNull(object o)
	{
		return object.ReferenceEquals(o, null);
	}

	private static Type T(string name)
	{
		try
		{
			return Type.GetType(name + ", Assembly-CSharp");
		}
		catch (Exception)
		{
			return null;
		}
	}

	private static void InitTypes()
	{
		if (IsNull(T_player))
		{
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
	}

	private static object GetField(object o, string name)
	{
		if (IsNull(o))
		{
			return null;
		}
		try
		{
			FieldInfo field = o.GetType().GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
			if (IsNull(field))
			{
				return null;
			}
			return field.GetValue(o);
		}
		catch (Exception)
		{
			return null;
		}
	}

	private static object GetProp(object o, string name)
	{
		if (IsNull(o))
		{
			return null;
		}
		try
		{
			PropertyInfo property = o.GetType().GetProperty(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
			if (!IsNull(property))
			{
				return property.GetValue(o, null);
			}
			FieldInfo field = o.GetType().GetField(name, BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic);
			if (!IsNull(field))
			{
				return field.GetValue(o);
			}
		}
		catch (Exception)
		{
		}
		return null;
	}

	private static int CountAny(object o)
	{
		if (IsNull(o))
		{
			return -1;
		}
		try
		{
			PropertyInfo property = o.GetType().GetProperty("Count");
			if (!IsNull(property))
			{
				return (int)property.GetValue(o, null);
			}
			ICollection collection = o as ICollection;
			if (!IsNull(collection))
			{
				return collection.Count;
			}
		}
		catch (Exception)
		{
		}
		return -1;
	}

	private static float X(object o)
	{
		try
		{
			Component component = o as Component;
			if (IsNull(component))
			{
				return float.NaN;
			}
			return component.transform.position.x;
		}
		catch (Exception)
		{
			return float.NaN;
		}
	}

	private static float Y(object o)
	{
		try
		{
			Component component = o as Component;
			if (IsNull(component))
			{
				return float.NaN;
			}
			return component.transform.position.y;
		}
		catch (Exception)
		{
			return float.NaN;
		}
	}

	private static string GoName(object o)
	{
		try
		{
			Component component = o as Component;
			if (IsNull(component))
			{
				return "-";
			}
			return component.gameObject.name;
		}
		catch (Exception)
		{
			return "-";
		}
	}

	private static string S(object v)
	{
		if (IsNull(v))
		{
			return "-";
		}
		try
		{
			if (v is float num)
			{
				return num.ToString("F2");
			}
			return v.ToString();
		}
		catch (Exception)
		{
			return "?";
		}
	}

	private static void Err(Exception e)
	{
		try
		{
			SB.Append("!E:").Append(e.GetType().Name).Append(",");
		}
		catch (Exception)
		{
		}
	}

	private static void Main()
	{
		InitTypes();
		if (IsNull(T_player))
		{
			return;
		}
		SB.Length = 0;
		SB.Append("KC|");
		UnityEngine.Object[] array = UnityEngine.Object.FindObjectsOfType(T_player);
		object o = null;
		int num = 0;
		if (num < array.Length)
		{
			o = array[num];
		}
		if (IsNull(o))
		{
			return;
		}
		float num2 = X(o);
		SB.Append("px=").Append(num2.ToString("F2"));
		SB.Append(",py=").Append(Y(o).ToString("F2"));
		SB.Append(",st=").Append(S(GetProp(o, "stamina")));
		try
		{
			SB.Append("|ps:").Append(S(GetField(o, "_payState")));
			SB.Append("|as:").Append(S(GetField(o, "_actionState")));
			object field = GetField(o, "_payTimer");
			if (!IsNull(field))
			{
				SB.Append("|pt:").Append(S(field));
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		try
		{
			object field2 = GetField(o, "_damageable");
			if (!IsNull(field2))
			{
				SB.Append(",hp=").Append(S(GetProp(field2, "hitPoints")));
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		int num3 = -1;
		try
		{
			object o2 = GetField(o, "_wallet");
			if (IsNull(o2))
			{
				UnityEngine.Object[] array2 = UnityEngine.Object.FindObjectsOfType(T_wallet);
				for (num = 0; num < array2.Length; num++)
				{
					object prop = GetProp(array2[num], "showCoinBag");
					if (!IsNull(prop) && prop.ToString() == "True")
					{
						o2 = array2[num];
						break;
					}
				}
			}
			if (!IsNull(o2))
			{
				num3 = Convert.ToInt32(GetProp(o2, "coins"));
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		SB.Append(",purse=").Append(num3);
		int value = -1;
		try
		{
			object field3 = GetField(o, "crownSprite");
			if (!IsNull(field3))
			{
				Component component = field3 as Component;
				if (!IsNull(component))
				{
					SpriteRenderer spriteRenderer = component as SpriteRenderer;
					if (!IsNull(spriteRenderer))
					{
						value = (spriteRenderer.enabled ? 1 : 0);
					}
				}
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		SB.Append(",hc=").Append(value);
		SB.Append("|crown:");
		try
		{
			GameObject[] array3 = GameObject.FindGameObjectsWithTag("Crown");
			bool flag = true;
			for (num = 0; num < array3.Length; num++)
			{
				if (!flag)
				{
					SB.Append(",");
				}
				flag = false;
				SB.Append(array3[num].transform.position.x.ToString("F1"));
			}
			if (flag)
			{
				SB.Append("-");
			}
		}
		catch (Exception e)
		{
			SB.Append("-");
			Err(e);
		}
		SB.Append("|sel:");
		try
		{
			object field4 = GetField(o, "_selectedPayable");
			if (!IsNull(field4))
			{
				SB.Append(field4.GetType().Name).Append(",").Append(Convert.ToInt32(GetProp(field4, "price")))
					.Append("@")
					.Append(X(field4).ToString("F1"))
					.Append("~")
					.Append(GoName(field4));
			}
			else
			{
				SB.Append("-");
			}
		}
		catch (Exception e)
		{
			SB.Append("-");
			Err(e);
		}
		int num4 = -1;
		try
		{
			UnityEngine.Object[] array4 = UnityEngine.Object.FindObjectsOfType(T_director);
			object o3 = null;
			if (array4.Length > 0)
			{
				o3 = array4[0];
			}
			if (!IsNull(o3))
			{
				num4 = Convert.ToInt32(GetProp(o3, "currentDay"));
				SB.Append("|day=").Append(num4);
				SB.Append(",time=").Append(S(GetProp(o3, "currentTime")));
				SB.Append(",playing=").Append(S(GetProp(o3, "playing")));
				SB.Append(",next=").Append(S(GetProp(o3, "nextEventName")));
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		int num5 = -1;
		int value2 = -1;
		int value3 = -1;
		int value4 = -1;
		int value5 = -1;
		int value6 = -1;
		int value7 = -1;
		int value8 = -1;
		int value9 = -1;
		int value10 = -1;
		try
		{
			UnityEngine.Object[] array5 = UnityEngine.Object.FindObjectsOfType(T_kingdom);
			object o4 = null;
			if (array5.Length > 0)
			{
				o4 = array5[0];
			}
			if (!IsNull(o4))
			{
				num5 = CountAny(GetField(o4, "_archers"));
				value2 = CountAny(GetField(o4, "_workers"));
				value3 = CountAny(GetField(o4, "_farmers"));
				value4 = CountAny(GetField(o4, "_knights"));
				value5 = CountAny(GetField(o4, "_walls"));
				value6 = CountAny(GetField(o4, "_shops"));
				value7 = CountAny(GetField(o4, "_farmlands"));
				value8 = CountAny(GetField(o4, "_portals"));
				value9 = CountAny(GetField(o4, "_beggarCamps"));
				value10 = CountAny(GetField(o4, "_availableWork"));
				SB.Append("|dayT=").Append(S(GetProp(o4, "isDaytime")));
				SB.Append("|safe=").Append(S(GetProp(o4, "isSafe")));
				SB.Append(",cf=").Append(S(GetProp(o4, "campfirePosition")));
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		SB.Append(",arc=").Append(num5);
		SB.Append(",wrk=").Append(value2);
		SB.Append(",frm=").Append(value3);
		SB.Append(",knt=").Append(value4);
		SB.Append(",wall=").Append(value5);
		SB.Append(",shop=").Append(value6);
		SB.Append(",farm=").Append(value7);
		SB.Append(",portal=").Append(value8);
		SB.Append(",camp=").Append(value9);
		SB.Append(",work=").Append(value10);
		int num6 = -1;
		try
		{
			UnityEngine.Object[] array6 = UnityEngine.Object.FindObjectsOfType(T_enemies);
			object o5 = null;
			if (array6.Length > 0)
			{
				o5 = array6[0];
			}
			if (!IsNull(o5))
			{
				num6 = CountAny(GetField(o5, "_enemies"));
				SB.Append(",atk=").Append(S(GetProp(o5, "enemiesAttacking")));
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		SB.Append(",en=").Append(num6);
		try
		{
			if (!IsNull(T_troll))
			{
				SB.Append(",troll=").Append(UnityEngine.Object.FindObjectsOfType(T_troll).Length);
			}
			if (!IsNull(T_ogre))
			{
				SB.Append(",ogre=").Append(UnityEngine.Object.FindObjectsOfType(T_ogre).Length);
			}
			if (!IsNull(T_squid))
			{
				SB.Append(",squid=").Append(UnityEngine.Object.FindObjectsOfType(T_squid).Length);
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		try
		{
			if (!IsNull(T_enemy))
			{
				UnityEngine.Object[] array7 = UnityEngine.Object.FindObjectsOfType(T_enemy);
				SB.Append("|enm:");
				for (num = 0; num < array7.Length; num++)
				{
					object prop2 = GetProp(array7[num], "isAttacking");
					SB.Append(GoName(array7[num])).Append("@").Append(X(array7[num]).ToString("F0"))
						.Append(":")
						.Append((!IsNull(prop2) && prop2.ToString() == "True") ? "1" : "0")
						.Append(",");
				}
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		try
		{
			if (!IsNull(T_coin))
			{
				UnityEngine.Object[] array8 = UnityEngine.Object.FindObjectsOfType(T_coin);
				int num7 = 0;
				StringBuilder stringBuilder = new StringBuilder();
				for (num = 0; num < array8.Length; num++)
				{
					float num8 = X(array8[num]);
					if (!float.IsNaN(num8) && !(Mathf.Abs(num8 - num2) > 40f))
					{
						num7++;
						stringBuilder.Append(num8.ToString("F0")).Append(",");
					}
				}
				SB.Append(",gcoin=").Append(num7);
				SB.Append("|gc:").Append(stringBuilder);
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		try
		{
			if (!IsNull(T_camp))
			{
				UnityEngine.Object[] array9 = UnityEngine.Object.FindObjectsOfType(T_camp);
				SB.Append("|camps:");
				for (num = 0; num < array9.Length; num++)
				{
					SB.Append(X(array9[num]).ToString("F0")).Append(",");
				}
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		try
		{
			if (!IsNull(T_portal))
			{
				UnityEngine.Object[] array10 = UnityEngine.Object.FindObjectsOfType(T_portal);
				SB.Append("|ports:");
				for (num = 0; num < array10.Length; num++)
				{
					Component component2 = array10[num] as Component;
					object o6 = ((!IsNull(component2) && !IsNull(T_damageable)) ? component2.GetComponent(T_damageable) : null);
					int value11 = ((!IsNull(o6)) ? Convert.ToInt32(GetProp(o6, "hitPoints")) : (-1));
					object prop3 = GetProp(array10[num], "outerSpawner");
					SB.Append(X(array10[num]).ToString("F0")).Append(":").Append(value11)
						.Append(":")
						.Append(IsNull(prop3) ? "?" : ((prop3.ToString() == "True") ? "1" : "0"))
						.Append(",");
				}
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		try
		{
			if (!IsNull(T_statue))
			{
				UnityEngine.Object[] array11 = UnityEngine.Object.FindObjectsOfType(T_statue);
				SB.Append("|stat:");
				for (num = 0; num < array11.Length; num++)
				{
					string value12 = S(GetProp(array11[num], "deity"));
					float num9 = -1f;
					object field5 = GetField(array11[num], "_currentCharge");
					if (!IsNull(field5))
					{
						num9 = Convert.ToSingle(field5);
					}
					SB.Append(value12).Append("@").Append(X(array11[num]).ToString("F0"))
						.Append(":")
						.Append(num9.ToString("F2"))
						.Append(",");
				}
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		try
		{
			if (!IsNull(T_guard))
			{
				UnityEngine.Object[] array12 = UnityEngine.Object.FindObjectsOfType(T_guard);
				SB.Append("|tow:");
				for (num = 0; num < array12.Length; num++)
				{
					object prop4 = GetProp(array12[num], "isAvailable");
					int value13 = ((!IsNull(prop4) && prop4.ToString() == "True") ? 1 : 0);
					SB.Append(X(array12[num]).ToString("F0")).Append(":").Append(value13)
						.Append(",");
				}
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		try
		{
			AppendCitizens(SB, T_archer, "A");
			AppendCitizens(SB, T_worker, "W");
			AppendCitizens(SB, T_farmer, "F");
			AppendCitizens(SB, T_knight, "K");
		}
		catch (Exception e)
		{
			Err(e);
		}
		try
		{
			if (!IsNull(T_peasant))
			{
				UnityEngine.Object[] array9 = UnityEngine.Object.FindObjectsOfType(T_peasant);
				for (num = 0; num < array9.Length; num++)
				{
					SB.Append("P@").Append(X(array9[num]).ToString("F0")).Append(",");
				}
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		try
		{
			if (!IsNull(T_beggar))
			{
				UnityEngine.Object[] array9 = UnityEngine.Object.FindObjectsOfType(T_beggar);
				for (num = 0; num < array9.Length; num++)
				{
					SB.Append("B@").Append(X(array9[num]).ToString("F0")).Append(",");
				}
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		try
		{
			if (!IsNull(T_knight))
			{
				UnityEngine.Object[] array13 = UnityEngine.Object.FindObjectsOfType(T_knight);
				SB.Append("|kng:");
				for (num = 0; num < array13.Length; num++)
				{
					string value14 = "?";
					try
					{
						object field6 = GetField(array13[num], "_fsm");
						object prop5 = GetProp(field6, "current");
						if (!IsNull(prop5))
						{
							value14 = prop5.ToString();
						}
					}
					catch (Exception)
					{
					}
					object prop6 = GetProp(array13[num], "side");
					SB.Append(X(array13[num]).ToString("F0")).Append(":").Append(value14)
						.Append(":")
						.Append(IsNull(prop6) ? "?" : prop6.ToString())
						.Append(",");
				}
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		try
		{
			if (!IsNull(T_tool))
			{
				UnityEngine.Object[] array14 = UnityEngine.Object.FindObjectsOfType(T_tool);
				SB.Append("|tool:");
				for (num = 0; num < array14.Length; num++)
				{
					SB.Append(GoName(array14[num])).Append("@").Append(X(array14[num]).ToString("F0"))
						.Append(",");
				}
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		try
		{
			if (!IsNull(T_wall))
			{
				UnityEngine.Object[] array15 = UnityEngine.Object.FindObjectsOfType(T_wall);
				SB.Append("|walls:");
				for (num = 0; num < array15.Length; num++)
				{
					Component component2 = array15[num] as Component;
					object o6 = ((!IsNull(component2) && !IsNull(T_damageable)) ? component2.GetComponent(T_damageable) : null);
					int value11 = ((!IsNull(o6)) ? Convert.ToInt32(GetProp(o6, "hitPoints")) : (-1));
					object prop7 = GetProp(array15[num], "level");
					SB.Append(X(array15[num]).ToString("F0")).Append(":").Append(value11)
						.Append(":")
						.Append(IsNull(prop7) ? "?" : prop7.ToString())
						.Append(",");
				}
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		try
		{
			if (!IsNull(T_farm))
			{
				UnityEngine.Object[] array16 = UnityEngine.Object.FindObjectsOfType(T_farm);
				SB.Append("|farms:");
				for (num = 0; num < array16.Length; num++)
				{
					object field7 = GetField(array16[num], "_isFarmed");
					SB.Append(X(array16[num]).ToString("F0")).Append(":").Append(IsNull(field7) ? "?" : field7.ToString())
						.Append(",");
				}
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		try
		{
			if (!IsNull(T_shop))
			{
				UnityEngine.Object[] array17 = UnityEngine.Object.FindObjectsOfType(T_shop);
				SB.Append("|shops:");
				for (num = 0; num < array17.Length; num++)
				{
					object prop8 = GetProp(array17[num], "itemPrefab");
					string value15 = "-";
					if (!IsNull(prop8))
					{
						Component component = prop8 as Component;
						value15 = (IsNull(component) ? prop8.ToString() : component.gameObject.name);
					}
					SB.Append(value15).Append("@").Append(X(array17[num]).ToString("F0"))
						.Append(",");
				}
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		try
		{
			if (!IsNull(T_payable))
			{
				UnityEngine.Object[] array18 = UnityEngine.Object.FindObjectsOfType(T_payable);
				SB.Append("|pay:");
				for (num = 0; num < array18.Length; num++)
				{
					float num8 = X(array18[num]);
					if (!float.IsNaN(num8))
					{
						SB.Append(array18[num].GetType().Name).Append(",").Append(Convert.ToInt32(GetProp(array18[num], "price")))
							.Append("@")
							.Append(num8.ToString("F1"))
							.Append("~")
							.Append(GoName(array18[num]))
							.Append(",");
					}
				}
			}
		}
		catch (Exception e)
		{
			Err(e);
		}
		Write(SB.ToString());
		try
		{
			if (num4 != _prevDay)
			{
				_prevDay = num4;
				Debug.Log("KCEVT|day=" + num4);
			}
			if (num3 != _prevPurse)
			{
				_prevPurse = num3;
				Debug.Log("KCEVT|coins=" + num3);
			}
			if (num6 != _prevEnemies)
			{
				_prevEnemies = num6;
				Debug.Log("KCEVT|enemies=" + num6);
			}
			if (num5 != _prevArchers)
			{
				_prevArchers = num5;
				Debug.Log("KCEVT|archers=" + num5);
			}
		}
		catch (Exception)
		{
		}
	}

	private static void AppendCitizens(StringBuilder sb, Type type, string tag)
	{
		if (IsNull(type))
		{
			return;
		}
		UnityEngine.Object[] array = UnityEngine.Object.FindObjectsOfType(type);
		for (int i = 0; i < array.Length; i++)
		{
			object field = GetField(array[i], "_wallet");
			int value = 0;
			if (!IsNull(field))
			{
				try
				{
					value = Convert.ToInt32(GetProp(field, "coins"));
				}
				catch (Exception)
				{
				}
			}
			sb.Append(tag).Append("@").Append(X(array[i]).ToString("F0"))
				.Append(":")
				.Append(value)
				.Append(",");
		}
	}

	private static void Write(string line)
	{
		try
		{
			if (IsNull(_path))
			{
				_path = Path.Combine(Application.dataPath, "kcdbg.txt");
			}
			File.AppendAllText(_path, line + "\n");
		}
		catch (Exception ex)
		{
			Debug.Log("KCERR|" + ex.Message);
		}
	}

	public static void Tick()
	{
		try
		{
			_speedAcc += Time.deltaTime;
			if (_speedAcc >= 1f)
			{
				_speedAcc = 0f;
				try
				{
					float result;
					if (!_speedInitialized)
					{
						_speedInitialized = true;
						string text = ((_path != null) ? Path.GetDirectoryName(_path) : null);
						if (text == null)
						{
							text = Path.Combine(Application.dataPath, "..");
						}
						string path = Path.Combine(text, "speed.txt");
						if (File.Exists(path))
						{
							string s = File.ReadAllText(path).Trim();
							if (float.TryParse(s, out result) && result > 0f && result <= 10f)
							{
								_currentSpeed = result;
								Time.timeScale = result;
							}
						}
						else
						{
							File.WriteAllText(path, "1");
						}
					}
					else
					{
						string text = ((_path != null) ? Path.GetDirectoryName(_path) : null);
						if (text == null)
						{
							text = Path.Combine(Application.dataPath, "..");
						}
						string path = Path.Combine(text, "speed.txt");
						if (File.Exists(path))
						{
							string s = File.ReadAllText(path).Trim();
							if (float.TryParse(s, out result) && result > 0f && result <= 10f &&
							    (result != _currentSpeed ||
							     (Time.timeScale > 0f && Time.timeScale != result)))
							{
								_currentSpeed = result;
								Time.timeScale = result;
							}
						}
					}
				}
				catch (Exception)
				{
				}
			}
			_acc += Time.deltaTime;
			if (!(_acc < 0.25f))
			{
				_acc = 0f;
				Main();
			}
		}
		catch (Exception ex2)
		{
			try
			{
				Write("KCERR|" + ex2.GetType().Name + "|" + ex2.Message + " @" + ex2.StackTrace);
			}
			catch (Exception)
			{
			}
			Debug.Log("KCERR|" + ex2.GetType().Name + "|" + ex2.Message + " @ " + ex2.StackTrace);
		}
	}
}
