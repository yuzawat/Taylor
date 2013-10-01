// show dialog
var show_dialog = function() {
    var cont_delete_confirm = $('#cont_delete_confirm')
    if (cont_delete_confirm) {
    	$.blockUI({message: cont_delete_confirm});
	console.log('delete_confirm');
    }
};

// show message
var show_message = function() {
    var message = $('#message').text();
    $('#message').text('');
    if (message) {
	$.growlUI(message);	  
	console.log(message);      
    }
};

var get_file_size = function() {
    var upfiles = document.getElementById("upfile").files;
    console.log(upfiles);
    console.log(upfiles[0].size);
    var elm = document.createElement('input');
    elm.setAttribute('type', 'hidden');
    elm.setAttribute('name', 'file_size');
    elm.setAttribute('value', upfiles[0].size);
    document.getElementById("upload_obj_values").appendChild(elm);
};

$(function() {
      $('#id_username').focus().end();
      show_message();

      $("div.dropzone").dropzone({url: $('form#upload_obj').attr('action'),
				  method: "POST",
				  paramName: "obj_name",
				  params: {"_action": "obj_create",
					   "obj_prefix": $('input[name=obj_prefix]').attr('value')},
				  clickable: false,
				  dictDefaultMessage: '',
				  createImageThumbnails: null});

  });
