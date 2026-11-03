# חתימת קוד ופרסום

## מסלול ההפצה

הפרויקט חינמי וברישיון MIT. מסלול ההפצה הפעיל אינו דורש תשלום או תעודת חתימה: מתקין לא חתום, עם `UNSIGNED` בשם הקובץ, SHA-256 והסבר מפורש על אזהרת Windows. תעודת חתימה היא אפשרות עתידית בלבד ואינה תנאי להפצה.

## פרסום גרסת ניסיון ללא חתימה

1. לעבוד מ־checkout נקי. התהליך `Windows installer candidate` בונה EXE באמצעות Inno Setup 7.1.1, בודק קוד, תכולת חבילה, התקנה, התקנה חוזרת והסרה.
2. בדיקת הפרסום נעשית באמצעות `scripts/verify_public_release.ps1 -Installer <exe> -Bundle <zip> -Unsigned -Preview -ExpectedCommit <commit>`. הבדיקה דורשת שמות קבצים מפורשים, מניפסט נקי, commit תואם וחותמות SHA-256. היא אינה טוענת שהושלמו בדיקות הקבלה במחשב נוסף.
3. להפיץ דרך Actions: לבחור `Windows installer candidate`, להפעיל `Run workflow` על `main` ולסמן `publish_preview`. ברירת המחדל היא ללא פרסום. בקשות משיכה ודחיפות רגילות אינן מפרסמות קבצים.
4. רק אחרי הצלחת הבדיקות מועלים ה־EXE וקובץ החותמת כ־GitHub `Pre-release`, עם tag מהצורה `v1.3.0-preview.<run number>`. הערות הפרסום נמצאות ב־[unsigned-release-notes.md](unsigned-release-notes.md). חבילת ZIP, נתוני קבלה ויומנים אישיים אינם מצורפים לפרסום.

הבדיקה עם `-Preview` מתאימה רק לפרסום המסומן `Pre-release`. לאחר השלמת בדיקות הקבלה של **אותו קובץ EXE**, ניתן לבדוק הפצה יציבה ללא תשלום בעזרת `scripts/verify_public_release.ps1 -Installer <exe> -Bundle <zip> -Unsigned -Acceptance <json>`. הדרישות למחשב נוסף, Windows 10/11 ושימור פרויקטים נשארות בתוקף. אם המתקין נבנה מחדש או השתנה, יש לבדוק את הקובץ החדש.

## אפשרות עתידית: תעודה על שם המפתח

בבדיקת עמוד הספק ב־3 בנובמבר 2026, המחיר ההתחלתי היה 49 אירו והמוצר סומן כלא זמין. יש לאמת מחדש זמינות, זכאות למפתח ישראלי, מסמכים ועלות סופית לפני תשלום. [מוצר](https://shop.certum.eu/open-source-code-signing-on-simplysign.html), [דרישות אימות](https://support.certum.eu/en/code-signing-required-documents/).

אם בוחרים בעתיד ב־Certum Open Source Code Signing ב־SimplySign, מאגר המקור הציבורי מקדים את הבקשה לתעודה. אימות זהות, מסמכים ותשלום נעשים ישירות אצל הספק; אין להכניסם למאגר, ליומני CI או לדיווח תקלה. מפתחות פרטיים נשארים ב־SimplySign. חתימה אינה הבטחה להיעדר אזהרת SmartScreen.

## בנייה וחתימה ידנית

1. לעבוד מ־checkout נקי של commit שנבדק. להתקין Inno Setup **7.1.1**, Windows SDK SignTool ו־SimplySign Desktop ממקוריהם הרשמיים.
2. להשלים את האימות מול Certum, לחבר את SimplySign Desktop ולפתוח הפעלת חתימה באמצעות היישומון. השם האישי המאומת, עם `Open Source Developer`, יופיע בתעודה.
3. לבחור במפורש את טביעת האצבע של התעודה המאומתת ולהגדיר אותה במשתנה הסביבה `KATUV_SIGN_CERT_SHA1`. זהו מזהה ציבורי של התעודה, לא מפתח פרטי. להגדיר `KATUV_SIGNTOOL` לנתיב המלא של SignTool ו־`KATUV_ISCC` לנתיב ISCC.
4. להריץ `python scripts/build_release.py --exe --signed`. הבנייה חותמת תחילה על סקריפטי PowerShell, מחשבת מניפסט לאחר החתימה, ובונה מתקין ומסיר חתומים דרך Inno Setup. כל חתימה מאומתת ונדרשת חותמת זמן. כישלון חתימה עוצר את הבנייה.
5. לבדוק את ה־EXE החתום עצמו ולמלא רישום קבלה מקומי לפי התבנית למטה. לחשב SHA-256 **לאחר** החתימה; שינוי בקובץ דורש בדיקה חדשה.
6. להריץ `scripts/verify_public_release.ps1 -Installer <exe> -Bundle <zip> -Acceptance <json> -PublisherThumbprint <thumbprint>`. אין לפרסם אם הפקודה נכשלת.
7. ליצור GitHub Release עם tag תואם לגרסה, לצרף את ה־EXE החתום, קובץ ה־SHA-256 שלו והערות הגרסה. ה־ZIP הוא חבילת ביקורת פנימית עם סקריפטי PowerShell חתומים; הוא אינו חתום בעצמו ואינו מצורף לפרסום. לא לצרף רישומי קבלה אישיים או מועמדים עם `UNSIGNED` בשמם.

הכלי `scripts/sign_file.ps1` משתמש ב־SHA-256 ובשרת חותמות הזמן של Certum. תהליך CI בונה ובודק ללא גישה לתעודה; הוא מפרסם רק גרסאות ניסיון לא חתומות בהפעלה מפורשת. אין צורך לשמור סיסמת SimplySign ב־GitHub.

## רישום קבלה

לשמור את הקובץ מחוץ למאגר או בשם `acceptance*.json` המוחרג מ־Git. כל ערך true דורש בדיקה שבוצעה בפועל. שני ערכי Windows דורשים בדיקה במערכות המתאימות; Windows Server של CI אינו תחליף. לפחות מחשב אחד חייב להיות שונה ממחשב הפיתוח.

```json
{
  "version": "1.3.0",
  "installer_sha256": "SHA256_OF_THE_EXACT_EXE_TO_PUBLISH",
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

לבדוק דיבור ושירה בעברית; טקסט מעורב; רזולוציות מקור עד 4K; אודיו ללא שינוי; עבודה אחרי ניתוק אינטרנט עם המודלים שהוכנו; ושימור תמליל ועריכות מפרויקט 1.2. אין לסמן דיוק שירה כאימות אנושי ללא האזנה בפועל. במסלול החתום יש לבדוק גם את שם המפרסם, תוקף החתימה וחותמת הזמן של `unins*.exe` אחרי ההתקנה. במסלול הלא חתום `signed_uninstaller` נשאר false ואינו נדרש.
