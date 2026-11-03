# חתימת קוד ופרסום

## מסלול ההפצה

הפרויקט חינמי וברישיון MIT. המסלול שנבחר הוא Certum Open Source Code Signing ב־SimplySign, עם חתימה ידנית של כל גרסה על שם המפתח. אין החלפה אוטומטית לספק יקר יותר ואין פרסום בינארי לא חתום.

בבדיקת עמוד הספק ב־3 בנובמבר 2026, המחיר ההתחלתי היה 49 אירו והמוצר סומן כלא זמין. יש לאמת מחדש זמינות, זכאות למפתח ישראלי, מסמכים ועלות סופית לפני תשלום. [מוצר](https://shop.certum.eu/open-source-code-signing-on-simplysign.html), [דרישות אימות](https://support.certum.eu/en/code-signing-required-documents/).

מאגר המקור הציבורי מקדים את הבקשה לתעודה. אימות זהות, מסמכים ותשלום נעשים ישירות אצל הספק; אין להכניסם למאגר, ליומני CI או לדיווח תקלה. מפתחות פרטיים נשארים ב־SimplySign. חתימה אינה הבטחה להיעדר אזהרת SmartScreen.

## בנייה וחתימה ידנית

1. לעבוד מ־checkout נקי של commit שנבדק. להתקין Inno Setup **7.1.1**, Windows SDK SignTool ו־SimplySign Desktop ממקוריהם הרשמיים.
2. להשלים את האימות מול Certum, לחבר את SimplySign Desktop ולפתוח הפעלת חתימה באמצעות היישומון. השם האישי המאומת, עם `Open Source Developer`, יופיע בתעודה.
3. לבחור במפורש את טביעת האצבע של התעודה המאומתת ולהגדיר אותה במשתנה הסביבה `KATUV_SIGN_CERT_SHA1`. זהו מזהה ציבורי של התעודה, לא מפתח פרטי. להגדיר `KATUV_SIGNTOOL` לנתיב המלא של SignTool ו־`KATUV_ISCC` לנתיב ISCC.
4. להריץ `python scripts/build_release.py --exe --signed`. הבנייה חותמת תחילה על סקריפטי PowerShell, מחשבת מניפסט לאחר החתימה, ובונה מתקין ומסיר חתומים דרך Inno Setup. כל חתימה מאומתת ונדרשת חותמת זמן. כישלון חתימה עוצר את הבנייה.
5. לבדוק את ה־EXE החתום עצמו ולמלא רישום קבלה מקומי לפי התבנית למטה. לחשב SHA-256 **לאחר** החתימה; שינוי בקובץ דורש בדיקה חדשה.
6. להריץ `scripts/verify_public_release.ps1 -Installer <exe> -Bundle <zip> -Acceptance <json> -PublisherThumbprint <thumbprint>`. אין לפרסם אם הפקודה נכשלת.
7. ליצור GitHub Release עם tag תואם לגרסה, לצרף רק את ה־EXE וה־ZIP החתומים ואת קובצי SHA-256, ולצרף את הערות הגרסה. לא לצרף רישומי קבלה אישיים או מועמדים עם `UNSIGNED` בשמם.

הכלי `scripts/sign_file.ps1` משתמש ב־SHA-256 ובשרת חותמות הזמן של Certum. תהליך CI בונה ובודק ללא גישה לתעודה ואינו מפרסם קובצי EXE/ZIP לא חתומים. אין צורך לשמור סיסמת SimplySign ב־GitHub.

## רישום קבלה

לשמור את הקובץ מחוץ למאגר או בשם `acceptance*.json` המוחרג מ־Git. כל ערך true דורש בדיקה שבוצעה בפועל. שני ערכי Windows דורשים בדיקה במערכות המתאימות; Windows Server של CI אינו תחליף. לפחות מחשב אחד חייב להיות שונה ממחשב הפיתוח.

```json
{
  "version": "1.3.0",
  "installer_sha256": "SHA256_OF_THE_FINAL_SIGNED_EXE",
  "second_computer": false,
  "tester": "",
  "tested_at": "",
  "checks": {
    "windows10_x64": false,
    "windows11_x64": false,
    "standard_user": false,
    "clean_install": false,
    "upgrade_1_2": false,
    "failed_upgrade": false,
    "restart": false,
    "busy_blocked": false,
    "port_conflict": false,
    "uninstall_preserves_projects": false,
    "offline_models": false,
    "hebrew_export": false,
    "signed_uninstaller": false
  }
}
```

לבדוק גם את שם המפרסם, תוקף החתימה וחותמת הזמן של `unins*.exe` אחרי ההתקנה; דיבור ושירה בעברית; טקסט מעורב; רזולוציות מקור עד 4K; אודיו ללא שינוי; עבודה אחרי ניתוק אינטרנט עם המודלים שהוכנו; ושימור תמליל ועריכות מפרויקט 1.2. אין לסמן דיוק שירה כאימות אנושי ללא האזנה בפועל.
