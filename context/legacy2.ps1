
#Main Functions
Function ChatGPT($query,$voice,$save,$append,$project){

#Flag for if we want JSON or Regular Text
if($json -eq "True"){

$body = @" 

{
    "model": "gpt-4-1106-preview",
    "messages": [{"role": "user", "content": "Please provide the following information in a plain JSON format without any Markdown or code block formatting: $query"}],
    "max_tokens": 4096
}

"@
}ELSE{
$body = @" 

{
    "model": "gpt-4-1106-preview",
    "messages": [{"role": "user", "content": "$query"}]
}

"@
}

$global:data = Invoke-RestMethod -Uri "https://api.openai.com/v1/chat/completions" -Headers $header -Body $body -Method Post
$global:GPTAnswer = $data.choices.message.content

#Turn into JSON Object or regular
if($json -eq "True"){

$GPTAnswer = $GPTAnswer -replace '```json',"" -replace '```',""

Write-Host -ForegroundColor Yellow "Returning JSON format"
$global:GPTAnswer = ($GPTAnswer | ConvertFrom-Json)
}

#Output Answer
write-host -foregroundcolor Cyan $global:GPTAnswer

#Save The File
if($save -eq "True"){

#If this is a youtube video save it as the video ID else, regular
if($VidID){

            if(Test-Path -path "D:\GPT\gptanswer-$vidID-$tag.txt"){
                $GPTAnswer | Out-File "D:\GPT\gptanswer-$vidID-$tag.txt" -Append
                }ELSE{
                #Write-Host No file found, saving for the first time
                $GPTAnswer | Out-File "D:\GPT\gptanswer-$vidID-$tag.txt"
                }
}

#If it's not a video
if(!($vid)){

$TP = (Test-Path -Path "D:\GPT\$project\")

if(!($TP)){
"Making Directory $Project"
mkdir "D:\GPT\$project\"
}

$county = gci D:\GPT\$project\
$global:count = $county.count
$GPTAnswer | Out-File "D:\GPT\$project\gptanswer-$project-$count.txt"
}


}

#Voice Answer
if($voice -eq "True"){
$global:SpeechAnswer = $GPTAnswer -replace "`n",", " -replace "`r",", "
$SpeechAnswer | Out-File -FilePath D:\GPT\$Project\Product.txt -Encoding ascii -Force
$SpeechAnswer = gc D:\GPT\$Project\Product.txt 
11TTS -TXT "$SpeechAnswer"
$global:AudioFile = "D:\GPT\$project\speech.mp3"
$global:voice = "False"
}






}
Function GPTImage($query,$amount){

if(!($amount)){
$amount = 10
}

$body = @" 

{
 
 "prompt": "$query",
 "n": $amount,
 "size": "1024x1024"
}

"@

$count = dir "D:\dwnld\AI-ART";$count = $count.count;$number = ($count + 1)
$global:data = Invoke-RestMethod -Uri "https://api.openai.com/v1/images/generations" -Headers $header -Body $body -Method Post
$global:tool = "Image Generation"
$date = get-date -Format "MM-dd-yyyy"

$global:results = $data.data.url
QueryDB -query $query 
$data.data.url | ForEach-Object{

Invoke-WebRequest -uri $_ -OutFile "$storageDir/$date-$count.png"
Start-Process chrome $_
$count = dir "D:\dwnld\AI-ART";$count = $count.count;$number = ($count + 1)}



}
Function Bard($query){


$global:bardanswer = $bardData.Split('[').split(']')
$bardanswer = $bardanswer[5]

$global:BardAnswer = $BardAnswer = "$BardAnswer" -replace "`n","" -replace "\\","" -replace "\\n","" -replace "\\r","" -replace '\"',""

write-host -ForegroundColor green Bard: $bardanswer
}
Function Models{

$global:data = Invoke-RestMethod -Uri "https://api.openai.com/v1/models" -Headers $header 

}
Function Speech($query){


$body = @" 

{
    "model": "tts-1",
    "input": "$query",
    "voice": "echo",
    "speed": "0.90"
}

"@

$global:data = Invoke-RestMethod -Uri "https://api.openai.com/v1/audio/speech" -Headers $header -Body $body -Method Post -OutFile "D:\GPT\$project\speech.mp3"

ii "D:\GPT\$project\speech.mp3"

#$global:GPTAnswer = $data.choices.message.content
}
Function Prompts($preface){

$global:NCurrentPrompt = "if there are technical instructions, list them. if there is an opertunity to gain financially from the information, list it and explain how this would work to my benefit, if there is a benefit, list the steps to do what the content is asking and ignore everything else."
$global:CurrentPrompt = "$Preface and now I want you to Summarize the message, highlighting the key points. Identify the most significant aspect and discuss its potential implications. List me off me 3 of the most impactful quotes or statements. Predict the future outcomes based on this information. If the message includes actionable steps, enumerate them and then describe based on your analysis what the theoretical outcome would be for me; if not, describe the overall sentiment. Lastly, provide a list of the most frequently mentioned keywords, unique terms, and main topics"
$global:OldCurrentPrompt = "summerize this message and list me the key points within it, advise me what you think the most important part of this message is and what possibilities can come from it, lastly let me know what you think potential future outcomes could be from this information, finally if there were any steps in this information list them for me to follow, otherwise give me the overall sentiment of this message, give me a list of the most referenced keywords, unique words or topics from this message"

$global:FilmotPrompt = "give me a 1 sentence summary only of what the text is about."
$global:HybridPrompt = "I want you to Summarize the message, highlighting the key points. Identify the most significant aspect and discuss its potential implications. List me off me 5 of the most impactful quotes or statements. If the message includes actionable steps, enumerate them and then describe based on your analysis what the impactful uses I could gain from them, otherwise say nothing. "

$global:IntelligenceAgent = "act as a intelligence analyist, give me a detailed report on this transcript"
$global:IntelligenceAgent2 = "act as a intelligence analyist, give me a detailed report on this transcript, include powerful quotes"

$global:Instructions = "list me all steps stated in this transcript, no summary"
$global:Instructions2 = "give me a summary if this level of work is worth it based on the transcript, if this revolves around business give me a theoretical estimated income monthly from doing something like this, else state this does not revolve around business. if this revolves around technology tell me how I would benefit from doing this if it's worth my time. give me links to any tools/services mentioned"

}
Function Prompt-Test{

$Tscripts = gci D:\GPT\transcripts

$Tscripts | ForEach-Object{

$Transcript = gc $_.FullName

ChatGPT -query "$CurrentPrompt - $Transcript" -save "False"
start-sleep -seconds 7
""
}

}
Function QueryDB($query){

#

                                    $global:QueryDB += New-Object PSObject -Property @{    
                                          
                                          'DATE' = get-date
                                          'Query' = $query
                                          'Tool' = $tool
                                          'Results' = $results

                                          }



$queryDB | Export-Csv -Path D:\scripts\queryDB.csv -NoTypeInformation
$queryDB = Import-Csv -Path D:\scripts\queryDB.csv

}

#Workflow Functions
Function GPT-Transcribe{
$global:header = @{  
               "Authorization" = "Bearer $key"


               }


$body = @{
    "model" = "whisper-1"
    "file" = "https://nicetalkingwithyou.com/wp-content/uploads/2018/07/006NTWY_U2_CL.mp3"
}




$global:data = Invoke-RestMethod -Uri "https://api.openai.com/v1/audio/transcriptions" -Headers $header -body $body -Method Post



$Uri = 'https://server/api/';
$Headers = @{'Auth_token'=$AUTH_TOKEN};
$FileContent = [IO.File]::('D:\test\test.test');
$Fields = @{'appInfo'='{"name": "test","description": "test"}';'uploadFile'=$FileContent};

Invoke-RestMethod -Uri $Uri -ContentType 'multipart/form-data' -Method Post -Headers $Headers -Body $Fields;




}
Function AWS-Transcript($file){

$file = gci $file
$global:filename = $file.Name

$global:Upload = Write-S3Object -BucketName gpttransscripts -ProfileName APIBoss -File $file.fullname

$global:S3ObjectURI = "s3://gpttransscripts/$filename"

$global:StartJob = Start-TRSTranscriptionJob -Region us-east-1 -TranscriptionJobName "$filename" -Media_MediaFileUri $S3ObjectURI -IdentifyLanguage $True -ProfileName APIBoss

$global:data = Get-TRSTranscriptionJob -ProfileName APIBoss -TranscriptionJobName "$filename" -Region us-east-1

while($data.TranscriptionJobStatus -eq "IN_PROGRESS"){
$global:data = Get-TRSTranscriptionJob -ProfileName APIBoss -TranscriptionJobName "$filename" -Region us-east-1
Write-Host -ForegroundColor green Waiting for Transcription....
Start-Sleep -Milliseconds 500
"$filename"
clear
}
if($data.TranscriptionJobStatus -eq "FAILED"){
write-host -ForegroundColor Red "JOB FAILED!!!!"
Remove-TRSTranscriptionJob -TranscriptionJobName "$filename" -Region us-east-1 -ProfileName APIBoss -Force -Confirm:$false
}
if($data.TranscriptionJobStatus -eq "COMPLETED"){
$global:Transcript = $data.Transcript.TranscriptFileUri

$global:T = Invoke-RestMethod -uri $Transcript

$global:T = $t.results.transcripts.transcript
write-host -foregroundcolor Green "Transcript Completed!"
Remove-TRSTranscriptionJob -TranscriptionJobName "$filename" -Region us-east-1 -ProfileName APIBoss -Force -Confirm:$false
#$T
}


}
Function DL-YT($URL){

$ID = $URL.Split('=')
$ID = $ID[1]
$global:VidID = $id
SubCheck -ytid $ID

if($SubCheck -eq "False"){
Get-ChildItem -Path D:\Youtube -Include *.* -File -Recurse | foreach { $_.Delete()}

Invoke-Expression "CD D:\youtube\"
Invoke-Expression "yt-dlp --windows-filenames -x --audio-format mp3 $url"

Start-Sleep -Seconds 10
$files = GCI 'D:\youtube\'
$x = $files.Count
$file = $files | sort lastwritetime -Descending | select -First 1
$global:file = $file.fullname
Start-Sleep -Seconds 4
$files = GCI 'D:\youtube\'
$files | sort lastwritetime -Descending | select -First 1 | Rename-Item -NewName "$x.mp3"
$files = GCI 'D:\youtube\'
$file = $files | sort lastwritetime -Descending | select -First 1
$global:file = $file.fullname
write-host -foregroundcolor green "$file"
}
}
Function YT-Summerize($YT,$preface,$GPTPrompt,$json,$style,$speak){

if($speak -eq "True"){
$global:voice = "True"
}

if($json -eq "True"){
$global:json = "True"
}else{$global:json = "False"}

prompts -preface $preface

$Global:YTLink = $YT
#Download Video
DL-YT -URL $YT

if($SubCheck -eq "False"){

#CheckFiles

#Transcribe
AWS-Transcript -file $file
Remove-TRSTranscriptionJob -TranscriptionJobName "$filename" -Region us-east-1 -ProfileName APIBoss -Force -Confirm:$false

#Send To GPT
if($T){

if($GPTPrompt){
ChatGPT -query "$GPTPrompt - $T" -save "True"
}ELSE{
switch ($style) {
    "intelligence" {
        # Action for intelligenceReport category
        Write-Host "Generating an intelligence report..."
        ChatGPT -query "$IntelligenceAgent - $T" -save "True"
    }
    "instructions" {
        # Action for instructionsReport category
        Write-Host "Generating an instructions report..."
        ChatGPT -query "$Instructions - $T" -save "True"
        ChatGPT -query "$Instructions2 - $T" -save "True"
    }
    "general" {
        # Action for generalReport category
        Write-Host "Generating a general report..."
        ChatGPT -query "$CurrentPrompt - $T" -save "True"
    }
    default {
        # Default action if none of the categories match
        Write-Host "Default Report."
        ChatGPT -query "$CurrentPrompt - $T" -save "True"
        # Add your code for handling an unknown category
    }
}
}


$t | out-file "D:\GPT\transcripts\transcript-$count.txt"
$Global:YTLink > "D:\GPT\transcripts\ytlink-$count.txt"

}ELSE{Write-Host -ForegroundColor Red "NO TRANSCRIPT CREATED"}
}

if($SubCheck -eq "True"){

if($GPTPrompt){
ChatGPT -query "$GPTPrompt - $T" -save "True"
}ELSE{
switch ($style) {
    "intelligence" {
        # Action for intelligenceReport category
        Write-Host "Generating an intelligence report..."
        $global:tag = "intelligence"
        ChatGPT -query "$IntelligenceAgent - $T" -save "True"

    }
    "instructions" {
        # Action for instructionsReport category
        Write-Host "Generating an instructions report..."
        $global:tag = "instructions"
        ChatGPT -query "$Instructions - $T" -save "True"
        ChatGPT -query "$Instructions2 - $T" -save "True"
    }
    "general" {
        # Action for generalReport category
        $global:tag = "general"
        Write-Host "Generating a general report..."
        ChatGPT -query "$CurrentPrompt - $T" -save "True"
    }
    default {
        # Default action if none of the categories match
        $global:tag = "general"
        Write-Host "Default Report."
        ChatGPT -query "$CurrentPrompt - $T" -save "True"
        # Add your code for handling an unknown category
    }
}



}

}

}
Function YT-Search($query,$order){

if(!($order)){
write-host "No specific order sent"
$order = "date"
}

#GetData1
$global:YT = Invoke-RestMethod -Method GET -Uri ("https://www.googleapis.com/youtube/v3/search?part=snippet&q=" + ("'" + $query + "'") + "&type=video&maxResults=10&order=$order&key=$YTKEY")

#Data
$global:videos = $YT.items.id.videoId
$global:videoInfo = $YT.items.snippet
$i = 0

#Enumerate
foreach($video in $videos){

$date = (get-date $videoInfo[$i].publishedAt)


Write-Host $date
write-host Channel: $videoInfo[$i].channelTitle
Write-Host -ForegroundColor Yellow Video: $videoInfo[$i].title
#write-host Description: $videoInfo[$i].description
""

$i++
}


}
Function YT-Channel($channel){
$global:YT = Invoke-RestMethod -Uri "https://www.googleapis.com/youtube/v3/search?key=$YTKEY&channelId=$channel&part=snippet,id&order=date&maxResults=10"

#Data
$global:videoItems = $YT.items.id.videoId
$global:videoInfo = $YT.items.snippet
$global:thumbnailPic = $videoInfo.thumbnails.high.url
$i = 0

#Enumerate
foreach($v in $videoItems){

$global:publishedAt = (get-date $videoInfo[$i].publishedAt -UFormat "%m/%d/%Y")
[string]$global:videoId = $videoItems[$i]
$global:channelId = $videoInfo[$i].channelId
$global:title = $videoInfo[$i].title
$global:description = $videoInfo[$i].description
$global:thumbnail = $thumbnailPic[$i]
$global:channelTitle = $videoInfo[$i].channelTitle
$global:publishTime = ((get-date $videoInfo[$i].publishTime -Format "hh:mm tt"))

#$title = $title -replace "'", "''"
#$description = $description -replace "'", "''"

#Action
VideoDB

Write-Host $date
write-host Channel: $videoInfo[$i].channelTitle
Write-Host -ForegroundColor Yellow Video: $videoInfo[$i].title
#write-host Description: $videoInfo[$i].description
""

#YT-Summerize -YT "https://www.youtube.com/watch?v=$targetV" -style instructions


$i++
}

}

Function VideoDB{
$global:TARGETDB = "UFOTube"
$global:Database = "D:\scripts\UFONewsNow\$TARGETDB.SQLite"
$global:DataSource = "D:\scripts\UFONewsNow\$TARGETDB.SQLite"

Invoke-SqliteQuery -DataSource $DataSource -Query "INSERT OR IGNORE INTO $TARGETDB (videoId, publishedAt, channelId, title, channelTitle, publishTime, description, thumbnail) VALUES('$videoId','$publishedAt','$channelId',@title,@channelTitle,'$publishTime',@description,'$thumbnail')"-SqlParameters @{
        
        title = [string]$title
        channelTitle = [string]$channelTitle
        description = [string]$description

    } 




}

#Youtube
Function YouTubeSources{





$RichardDolan = "UCnaIeNm-jSa1l8yHrn7PlQg"
$RossCoulthart = "UCVNKdkLzWuy1oLuCuCv4NCA"
$ThatUFOPodcast = "UCHw9Lru3EcpRQyM7AI5TlmA"
$SpacedOutRadio = "UCtBgznsvndqzDt_wogiR8Gw"
$NewsNation = "UCCjG8NtOig0USdrT5D1FpxQ"
$DisclosureTEAM = "UCMEnA8bwyz4-JVDBkLgcntg"
$JeremyCorbell = "UCkgPT7LeB_t1aXTYyMiFVAg"
$WhitleyStrieber = "UCx3VUje5-dpBh4Q_LZBsDgA"
$LindaHowe = "UCN9WjlKBvjBIm3AWDXI1EUA"
$EngagingThePhenomenon = "UCnFc2oM4A60NcG0LCjKoH8g"
$TheGoodTroubleShow = "UC92qnF5yG2VnrSfz77OALAw"

$Global:UFOArray = ($RichardDolan,$RossCoulthart,$ThatUFOPodcast,$SpacedOutRadio,$NewsNation,$DisclosureTEAM,$JeremyCorbell,$WhitleyStrieber,$LindaHowe,$EngagingThePhenomenon,$TheGoodTroubleShow)


$UFOArray | ForEach-Object{YT-Channel $_}

$global:DBdata = Invoke-SqliteQuery -DataSource $DataSource -Query "SELECT * FROM $TARGETDB";$data.count

}

Function Filmot($query,$lots,$Prompt,$project){
$headers=@{}
$headers.Add("X-RapidAPI-Key", "666e1bd66cmsh8311b98137a286cp17765bjsn64efd9920793")
$headers.Add("X-RapidAPI-Host", "filmot-tube-metadata-archive.p.rapidapi.com")

$query = ('"'+$query+'"')

$global:response = Invoke-RestMethod -Uri ("https://filmot-tube-metadata-archive.p.rapidapi.com/getsubtitlesearch?query=$query") -Method GET -Headers $headers
#$response.result | select title,viewcount
""

$title = $response.result.title

$global:Query = $query
$response.more_results | select title,viewcount


$global:id = $response.result.id
[string]$global:Sub = ($response.subtitles.txt)
[string]$sub | Out-File "D:\GPT\subs\$id.txt" -Force -Encoding ASCII 
[string]$sub = gc "D:\GPT\subs\$id.txt" 

$global:project = $Project

Write-Host -ForegroundColor Red FILMOT $id
ChatGPT -query "$Prompt - $sub" -save "True"

if($lots -eq "True"){
Next-Vid -query $query -prompt $prompt 
}

}
Function Get-Vid($i,$Prompt){

$headers=@{}
$headers.Add("X-RapidAPI-Key", "666e1bd66cmsh8311b98137a286cp17765bjsn64efd9920793")
$headers.Add("X-RapidAPI-Host", "filmot-tube-metadata-archive.p.rapidapi.com")
$global:response = Invoke-RestMethod -Uri "https://filmot-tube-metadata-archive.p.rapidapi.com/getsubtitlesearch?query=$query&queryVideoID=$i" -Method GET -Headers $headers

$global:response.result | select title,viewcount,id 
$global:id = $global:response.result.id
Write-Host -ForegroundColor Red Get-Vid "$id"


$global:id = $response.result.id
[string]$global:Sub = ($response.subtitles.txt)
[string]$sub | Out-File "D:\GPT\subs\$id.txt" -Force -Encoding ASCII 
#[string]$sub = gc "D:\GPT\subs\$id.txt" 
ChatGPT -query ("$prompt - " + ($response.subtitles.txt)) -save "True"
#gc $global:sub -Tail 1


#$response.result | select title,viewcount

#$response.more_results | select title,viewcount

#$global:Sub = $response.subtitles.txt
}
Function Next-Vid($query,$prompt){


#Cycle Through everysingle one
$global:response.more_results.id | ForEach-Object{ 
Write-Host -ForegroundColor Red NEXT-VID $_
get-vid -i $_ -Prompt $Prompt

#ChatGPT -query "$FilmotPrompt - $sub" -save "False"
}
#$response.result | select title,viewcount
""
}

#Check if there are Subs Available for free
Function SubCheck($ytid){
Write-Host -ForegroundColor Green Checking on $ytid
Invoke-Expression "python D:\scripts\getTranscript.py $ytid"


$global:check = gci D:\GPT\subs\$ytid.txt

if($check.Length -gt 0){
$global:SubCheck = "True"
Write-Host -ForegroundColor Green "SUBS SAVED"
$global:T = GC "D:\GPT\subs\$ytid.txt"
}ELSE{
Write-Host -ForegroundColor Red "UNABLE TO DOWNLOAD SUB"
$global:SubCheck = "False"
}

}
Function Create-Video($videofile,$audiofile,$outputfile){

Invoke-Expression "python D:\scripts\movieMerge.py $videofile $audiofile $outputfile"


}


Function Story-Workflow($Story,$project,$storytitle){

$project = $global:project

write-host -foregroundcolor GREEN "Creating Story"
ChatGPT -query $Story -voice "True" -save "True" -project $project

write-host -foregroundcolor GREEN "Creating Video"
Create-Video -videofile "D:\Users\GodLife\Downloads\hybrid.mp4" -audiofile $audiofile -outputfile "D:\Users\GodLife\Downloads\Final_Output.mp4"

#write-host -foregroundcolor GREEN "Creating Subtitles"
#Add-Subtitles -targetfile D:\Users\GodLife\Downloads\Final_Output.mp4

write-host -foregroundcolor GREEN "Uploading Video to Youtube"
Youtube-Upload -accountName "personal" -videoFilePath 'D:\Users\GodLife\Downloads\Final_Output.mp4'`
-title "$storyTitle"`
-description "THIS IS AN API TEST"`
-categoryID 22 `
-keywords "API,TEST" `
-privacyStatus "Public" `
-madeForKids "false" `


}

Function Add-Subtitles($targetfile){
#Set The File to upload
$file = gci $targetfile
$global:filename = $file.Name
#Upload to S3
$global:Upload = Write-S3Object -BucketName gpttransscripts -ProfileName APIBoss -File $file.fullname
$global:S3ObjectURI = "s3://gpttransscripts/$filename"
$URL = Get-S3PreSignedURL -BucketName gpttransscripts -key "$filename"  -ProfileName APIBoss -Expire "2024-11-16"
$global:DirectLink = $URL


#Create Subtitles
# Define API URL
$apiUrl = "https://api.bannerbear.com/v2/videos"  # Replace with the correct API endpoint

# Set your API Key
$apiKey = "bb_ma_fcf461ce66fe14cfabe470d1c68a70"
$headers = @{
    "Authorization" = "Bearer $apiKey"
    "Content-Type" = "application/json"
}
$body = @{
    "template" = "eBgAXO9vjpo9rpZJo1"  # Replace with your template ID
    "project_id" = "EGBqpAZ55B9Z89VDNJ"  # Add your project ID here
    "video_template" = "eBgAXO9vjpo9rpZJo1"
    "input_media_url" = $directlink

} | ConvertTo-Json

# Send the POST request with error handling
try{
    $response = Invoke-RestMethod -Uri $apiUrl -Method Post -Headers $headers -Body $body
    $global:UID = $response.uid

    $headers = @{
    "Authorization" = "Bearer $apiKey"
}
$queryParams = @{
    "project_id" = "EGBqpAZ55B9Z89VDNJ"
}
$global:response = Invoke-RestMethod -Uri "$ApiUrl/$uid" -Method Get -Headers $Headers -Body $QueryParams
# Function to check the video status with error logging



} catch{
    Write-Host "Error: $($_.Exception.Response.StatusCode.Value__) - $($_.Exception.Response.StatusDescription)"
    $responseStream = $_.Exception.Response.GetResponseStream()
    $reader = New-Object System.IO.StreamReader($responseStream)
    $responseBody = $reader.ReadToEnd()
    Write-Host "Response body: $responseBody"
}


while($response.status -eq "pending"){
    $headers = @{
    "Authorization" = "Bearer $apiKey"
}
    $queryParams = @{
    "project_id" = "EGBqpAZ55B9Z89VDNJ"
}

    $global:response = Invoke-RestMethod -Uri "$ApiUrl/$uid" -Method Get -Headers $Headers -Body $QueryParams

$response.status
$response
Start-Sleep -Seconds 60

}
if($response.status -eq "completed"){
write-host -ForegroundColor GREEN FINISHED SUBS
$global:FinalVideo = "https://videos.bannerbear.com/completed/movie-0J62MGo2JWMboLaAV3.mp4"
Invoke-RestMethod -Uri $FinalVideo -OutFile "D:\scripts\subvid.mp4"
$global:FinalVideo = "D:\scripts\subvid.mp4"


}
if($response.status -eq "failed"){
write-host -ForegroundColor Red FAILED!!!!
}





}

Function GoogleTTS{


    $params = @{q="$text";target="en";format="text";key=$global:APIKey}  



}
Function 11TTS($vocal,$TXT){

$vocal = "XB0fDUnXU5powFXDhCwa"


$11header = @{  
               "xi-api-key" = "$11key"
               "Content-Type" = "application/json"
               }

$body = @" 

{
  "text": "$TXT",
  "voice_settings": {
    "similarity_boost": 1,
    "stability": 1,
    "use_speaker_boost": true
  },
  "model_id": "eleven_multilingual_v2"
}

"@

$global:TTS = Invoke-RestMethod -Uri ("https://api.elevenlabs.io/v1/text-to-speech/" + ($vocal) + "?output_format=mp3_44100_128") -Body $body -Headers $11header -Method Post -OutFile "D:\GPT\$project\speech.mp3"

$global:AudioFile = "D:\GPT\$project\speech.mp3"
#ii "D:\GPT\$project\speech.mp3"



}

Function YT-Upload{

$accountName = "account1"  # Replace with your account name
$videoFilePath = "path\to\your\video.mp4"  # Replace with the path to your video
$title = "Your Video Title"  # Replace with your video's title
$description = "Your Video Description"  # Replace with your video's description
$categoryID = "22"  # Replace with your video's category ID
$keywords = "keyword1,keyword2"  # Replace with your video's keywords
$privacyStatus = "public"  # Replace with 'public', 'private', or 'unlisted'
$madeForKids = "true"  # Replace with 'true' if the video is made for kids, 'false' otherwise

# Command to run the Python script with all arguments
python D:\scripts\uploadYT.py $accountName $videoFilePath $title $description $categoryID $keywords $privacyStatus $madeForKids

#Youtube-Upload -accountName "account1" -videoFilePath "D:\path\to\video.mp4" -title "My Video Title" -description "This is a description of my video" -categoryID "22" -keywords "keyword1,keyword2" -privacyStatus "public" -madeForKids "false"



}
Function Youtube-Upload {
    param(
        [string]$accountName,
        [string]$videoFilePath,
        [string]$title,
        [string]$description,
        [string]$categoryID,
        [string]$keywords,
        [string]$privacyStatus,
        [string]$madeForKids
    )

    # Command to run the Python script with all arguments
    python D:\scripts\uploadYT.py $accountName $videoFilePath $title $description $categoryID $keywords $privacyStatus $madeForKids

#Youtube-Upload -accountName "account1" -videoFilePath "D:\path\to\video.mp4" -title "My Video Title" -description "This is a description of my video" -categoryID "22" -keywords "keyword1,keyword2" -privacyStatus "public" -madeForKids "false"
}


